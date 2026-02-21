from __future__ import absolute_import, unicode_literals

import signal
import socket
import struct

import dnsdle.cname_payload as cname_payload
import dnsdle.dnswire as dnswire
from dnsdle.constants import DNS_FLAG_QR
from dnsdle.constants import DNS_OPCODE_QUERY
from dnsdle.constants import DNS_QCLASS_IN
from dnsdle.constants import DNS_QTYPE_A
from dnsdle.constants import DNS_RCODE_NOERROR
from dnsdle.constants import DNS_RCODE_NXDOMAIN
from dnsdle.constants import DNS_RCODE_SERVFAIL
from dnsdle.constants import DNS_UDP_RECV_MAX
from dnsdle.helpers import labels_is_suffix
from dnsdle.console import console_activity
from dnsdle.console import console_error
from dnsdle.console import console_server_start
from dnsdle.console import console_shutdown
from dnsdle.logging_runtime import log_event
from dnsdle.logging_runtime import logger_enabled
from dnsdle.state import StartupError


def _selected_domain(config, qname_labels):
    for index, labels in enumerate(config.domain_labels_by_domain):
        if labels_is_suffix(labels, qname_labels):
            prefix_len = len(qname_labels) - len(labels)
            return config.domains[index], tuple(qname_labels[:prefix_len])
    return None, None


def _build_log(classification, reason_code, context=None):
    record = {
        "classification": classification,
        "phase": "server",
        "reason_code": reason_code,
    }
    if context:
        record.update(context)
    return record


def _classified_response(request, config, rcode, classification, reason_code, context):
    response = dnswire.build_response(
        request,
        rcode,
        answer_bytes=None,
        include_opt=config.dns_edns_size > 512,
        edns_size=config.dns_edns_size,
    )
    return response, _build_log(classification, reason_code, context)


def _envelope_miss_reason(request, config):
    if request["flags"] & DNS_FLAG_QR:
        return "invalid_query_flags", {"flags": request["flags"]}
    if request["opcode"] != DNS_OPCODE_QUERY:
        return "unsupported_opcode", {"opcode": request["opcode"]}
    if request["qdcount"] != 1 or request["ancount"] != 0 or request["nscount"] != 0:
        return "invalid_query_section_counts", {
            "qdcount": request["qdcount"],
            "ancount": request["ancount"],
            "nscount": request["nscount"],
            "arcount": request["arcount"],
        }
    invalid_ar = request["arcount"] != 0 if config.dns_edns_size == 512 else request["arcount"] > 1
    if invalid_ar:
        return "invalid_additional_count", {
            "arcount": request["arcount"],
            "dns_edns_size": config.dns_edns_size,
        }
    return None, None


def handle_request_message(runtime_state, request_bytes):
    config = runtime_state.config
    try:
        request = dnswire.parse_request(request_bytes)
    except dnswire.DnsParseError:
        return None, None
    if logger_enabled("trace"):
        log_event(
            "trace",
            "server",
            {
                "phase": "server",
                "classification": "diagnostic",
                "reason_code": "request_parsed",
            },
            context_fn=lambda: {
                "request_len": len(request_bytes),
                "qdcount": request.get("qdcount"),
            },
        )

    miss_reason, miss_context = _envelope_miss_reason(request, config)
    if miss_reason is not None:
        return _classified_response(request, config, DNS_RCODE_NXDOMAIN, "miss", miss_reason, miss_context)

    question = request.get("question")
    if question is None:
        return _classified_response(request, config, DNS_RCODE_NXDOMAIN, "miss", "missing_question", None)

    qname_labels = question["qname_labels"]
    selected_domain, prefix_labels = _selected_domain(config, qname_labels)
    if selected_domain is None:
        return _classified_response(request, config, DNS_RCODE_NXDOMAIN, "miss", "unknown_domain", None)

    qtype = question["qtype"]
    qclass = question["qclass"]
    if qtype != DNS_QTYPE_A or qclass != DNS_QCLASS_IN:
        return _classified_response(
            request, config, DNS_RCODE_NOERROR, "miss",
            "unsupported_qtype_or_class",
            {"qtype": qtype, "qclass": qclass},
        )

    if len(prefix_labels) >= 2 and prefix_labels[-1] == config.response_label:
        answer_bytes = dnswire.build_a_answer(config.ttl)
        response = dnswire.build_response(
            request,
            DNS_RCODE_NOERROR,
            answer_bytes=answer_bytes,
            include_opt=config.dns_edns_size > 512,
            edns_size=config.dns_edns_size,
        )
        return response, _build_log(
            "followup",
            "followup_a_response",
            {"selected_base_domain": selected_domain},
        )

    if len(prefix_labels) != 2:
        return _classified_response(
            request, config, DNS_RCODE_NOERROR, "miss",
            "invalid_slice_qname_shape",
            {
                "selected_base_domain": selected_domain,
                "label_count_before_domain": len(prefix_labels),
            },
        )

    slice_token = prefix_labels[0]
    file_tag = prefix_labels[1]
    request_context = {
        "selected_base_domain": selected_domain,
        "file_tag": file_tag,
        "slice_token": slice_token,
    }
    key = (file_tag, slice_token)
    identity_value = runtime_state.lookup_by_key.get(key)
    if identity_value is None:
        return _classified_response(request, config, DNS_RCODE_NXDOMAIN, "miss", "mapping_not_found", request_context)

    file_id, publish_version, slice_index = identity_value
    if logger_enabled("debug"):
        log_event(
            "debug",
            "server",
            {
                "phase": "server",
                "classification": "diagnostic",
                "reason_code": "mapping_resolved",
            },
            context_fn=lambda: {
                "selected_base_domain": selected_domain,
                "file_tag": file_tag,
                "slice_token": slice_token,
                "file_id": file_id,
                "slice_index": slice_index,
            },
        )
    identity = (file_id, publish_version)
    slice_data = runtime_state.slice_data_by_identity.get(identity)
    if slice_data is None:
        return _classified_response(request, config, DNS_RCODE_SERVFAIL, "runtime_fault", "identity_missing", request_context)
    slice_table, compressed_size = slice_data
    total_slices = len(slice_table)
    if slice_index < 0 or slice_index >= total_slices:
        return _classified_response(
            request, config, DNS_RCODE_SERVFAIL, "runtime_fault",
            "slice_index_out_of_bounds",
            dict(request_context, slice_index=slice_index, slice_count=total_slices),
        )

    slice_bytes = slice_table[slice_index]
    try:
        payload_labels = cname_payload.payload_labels_for_slice(
            config.psk,
            file_id,
            publish_version,
            slice_index,
            total_slices,
            compressed_size,
            slice_bytes,
            config.dns_max_label_len,
        )
        answer_bytes = dnswire.build_cname_answer(
            qname_labels,
            2,
            payload_labels,
            config.response_label,
            config.ttl,
        )
        response = dnswire.build_response(
            request,
            DNS_RCODE_NOERROR,
            answer_bytes=answer_bytes,
            include_opt=config.dns_edns_size > 512,
            edns_size=config.dns_edns_size,
        )
    except Exception as exc:
        return _classified_response(
            request, config, DNS_RCODE_SERVFAIL, "runtime_fault",
            "encode_failure",
            dict(request_context, message=str(exc)),
        )

    return response, _build_log(
        "served",
        "slice_served",
        {
            "selected_base_domain": selected_domain,
            "file_tag": file_tag,
            "slice_token": slice_token,
            "file_id": file_id,
            "publish_version": publish_version,
            "slice_index": slice_index,
        },
    )


def _validate_runtime_state_for_serving(runtime_state):
    config = runtime_state.config
    query_token_len = runtime_state.budget_info.get("query_token_len")
    if query_token_len is None:
        raise StartupError(
            "startup",
            "server_runtime_invalid",
            "budget_info missing query_token_len",
        )

    question_labels = (
        "a" * query_token_len,
        "b" * config.file_tag_len,
    ) + tuple(config.longest_domain_labels)

    try:
        payload_labels = cname_payload.payload_labels_for_slice(
            config.psk,
            "0" * 16,
            "1" * 64,
            0,
            1,
            1,
            b"x",
            config.dns_max_label_len,
        )
        answer = dnswire.build_cname_answer(
            question_labels,
            2,
            payload_labels,
            config.response_label,
            config.ttl,
        )
        dnswire.build_response(
            {
                "id": 0,
                "flags": 0,
                "raw_question_bytes": (
                    dnswire.encode_name(question_labels)
                    + struct.pack("!HH", DNS_QTYPE_A, DNS_QCLASS_IN)
                ),
            },
            DNS_RCODE_NOERROR,
            answer_bytes=answer,
            include_opt=config.dns_edns_size > 512,
            edns_size=config.dns_edns_size,
        )
    except Exception as exc:
        raise StartupError(
            "startup",
            "server_runtime_invalid",
            "runtime response encoding invariant check failed: %s" % exc,
        )


def serve_runtime(runtime_state, emit_record, stop_requested=None, display_names=None):
    _validate_runtime_state_for_serving(runtime_state)
    config = runtime_state.config

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((config.listen_host, config.listen_port))
    except socket.error as exc:
        sock.close()
        raise StartupError(
            "startup",
            "bind_failed",
            "failed to bind UDP listener: %s" % exc,
            {
                "listen_host": config.listen_host,
                "listen_port": config.listen_port,
            },
        )

    if display_names is None:
        display_names = {}
    seen_tags = set()
    counters = {
        "served": 0,
        "followup": 0,
        "miss": 0,
        "runtime_fault": 0,
        "dropped": 0,
    }
    stop_state = {"stop": False, "reason": "stop_requested"}

    def _request_stop(signum, _frame):
        stop_state["stop"] = True
        stop_state["reason"] = "signal_%s" % signum

    signal_handlers = {}
    for signal_name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signal_name):
            sig = getattr(signal, signal_name)
            signal_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _request_stop)

    sock.settimeout(0.5)
    emit_record(
        {
            "classification": "server_start",
            "phase": "server",
            "listen_host": config.listen_host,
            "listen_port": config.listen_port,
        }
    )
    console_server_start(config.listen_host, config.listen_port)

    try:
        while not stop_state["stop"]:
            if stop_requested is not None and stop_requested():
                stop_state["stop"] = True
                stop_state["reason"] = "stop_callback"
                break

            try:
                datagram, addr = sock.recvfrom(DNS_UDP_RECV_MAX)
            except socket.timeout:
                if logger_enabled("trace"):
                    log_event(
                        "trace",
                        "server",
                        {
                            "phase": "server",
                            "classification": "diagnostic",
                            "reason_code": "loop_timeout",
                        },
                    )
                continue
            except KeyboardInterrupt:
                stop_state["stop"] = True
                stop_state["reason"] = "keyboard_interrupt"
                continue
            except socket.error as exc:
                emit_record(
                    _build_log(
                        "runtime_fault",
                        "recv_error",
                        {"message": str(exc)},
                    )
                )
                console_error("recv_error: %s" % exc)
                counters["runtime_fault"] += 1
                continue

            try:
                response_bytes, log_record = handle_request_message(runtime_state, datagram)
            except Exception as exc:
                counters["runtime_fault"] += 1
                emit_record(
                    _build_log(
                        "runtime_fault",
                        "unhandled_request_exception",
                        {"message": str(exc)},
                    )
                )
                console_error("unhandled_request_exception: %s" % exc)
                continue

            if response_bytes is None:
                counters["dropped"] += 1
                continue

            try:
                sock.sendto(response_bytes, addr)
            except socket.error as exc:
                counters["runtime_fault"] += 1
                emit_record(
                    _build_log(
                        "runtime_fault",
                        "send_error",
                        {"message": str(exc)},
                    )
                )
                console_error("send_error: %s" % exc)
                continue

            if log_record is not None:
                classification = log_record.get("classification")
                if classification in counters:
                    counters[classification] += 1
                if classification == "served":
                    file_tag = log_record.get("file_tag")
                    if file_tag is not None and file_tag not in seen_tags:
                        seen_tags.add(file_tag)
                        console_activity(
                            file_tag,
                            display_names.get(file_tag, file_tag),
                        )
                emit_record(log_record)
    finally:
        for sig, previous_handler in signal_handlers.items():
            signal.signal(sig, previous_handler)
        sock.close()

    console_shutdown(counters)
    emit_record(
        {
            "classification": "shutdown",
            "phase": "server",
            "reason_code": stop_state["reason"],
            "served": counters["served"],
            "followup": counters["followup"],
            "miss": counters["miss"],
            "runtime_fault": counters["runtime_fault"],
            "dropped": counters["dropped"],
        }
    )
    return 0
