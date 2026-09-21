from amazai.stream import EventKind, StreamParser


def _drain(parser, events):
    out = []
    for e in events:
        out.extend(parser.feed(e))
    out.extend(parser.flush())
    return out


class TestText:
    def test_text_deltas_are_emitted(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Hello "}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "world"}}},
        ])
        assert "".join(e.text for e in out if e.kind is EventKind.TEXT) == "Hello world"

    def test_empty_text_delta_is_not_emitted(self):
        p = StreamParser()
        out = _drain(p, [{"contentBlockDelta": {"delta": {"text": ""}}}])
        assert not [e for e in out if e.kind is EventKind.TEXT]


class TestToolUseShapes:
    def test_input_complete_on_start(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {
                "name": "pr.create", "toolUseId": "tu-1",
                "input": {"repo": "amazai"}}}}},
            {"contentBlockStop": {"contentBlockIndex": 1}},
        ])
        tool = [e for e in out if e.kind is EventKind.TOOL_USE][0]
        assert tool.tool_name == "pr.create"
        assert tool.tool_input == {"repo": "amazai"}

    def test_input_accumulated_across_partial_json(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
                "name": "shell", "toolUseId": "tu-2"}}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
                "partial_json": '{"command":'}}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
                "partial_json": '"pytest -x"}'}}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
        ])
        tool = [e for e in out if e.kind is EventKind.TOOL_USE][0]
        assert tool.tool_input == {"command": "pytest -x"}

    def test_input_arriving_as_a_json_string(self):
        # The shape BUILD_PLAN warns about explicitly.
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
                "name": "shell", "toolUseId": "tu-3", "input": '{"command":"ls"}'}}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
        ])
        tool = [e for e in out if e.kind is EventKind.TOOL_USE][0]
        assert tool.tool_input == {"command": "ls"}

    def test_toolusage_without_start_block(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockDelta": {"contentBlockIndex": 5, "delta": {"toolUse": {
                "name": "browser", "toolUseId": "tu-4",
                "input": {"url": "https://example.com"}}}}},
        ])
        tool = [e for e in out if e.kind is EventKind.TOOL_USE][0]
        assert tool.tool_name == "browser"

    def test_two_concurrent_tool_blocks_do_not_mix(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
                "name": "shell", "toolUseId": "a"}}}},
            {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {
                "name": "browser", "toolUseId": "b"}}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
                "partial_json": '{"command":"ls"}'}}}},
            {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {
                "partial_json": '{"url":"x"}'}}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
            {"contentBlockStop": {"contentBlockIndex": 1}},
        ])
        tools = {e.tool_name: e.tool_input for e in out if e.kind is EventKind.TOOL_USE}
        assert tools["shell"] == {"command": "ls"}
        assert tools["browser"] == {"url": "x"}


class TestMalformedInput:
    def test_unparsable_json_does_not_raise(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
                "name": "shell", "toolUseId": "tu-5", "input": '{"command": '}}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
        ])
        tool = [e for e in out if e.kind is EventKind.TOOL_USE][0]
        assert "_unparsed" in tool.tool_input

    def test_missing_input_becomes_empty_dict(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
                "name": "shell", "toolUseId": "tu-6"}}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
        ])
        assert [e for e in out if e.kind is EventKind.TOOL_USE][0].tool_input == {}


class TestErrors:
    def test_runtime_client_error_is_surfaced(self):
        p = StreamParser()
        out = p.feed({"runtimeClientError": {"message": "model unavailable"}})
        assert out[0].kind is EventKind.ERROR
        assert "model unavailable" in out[0].error

    def test_error_without_message_still_classifies(self):
        p = StreamParser()
        assert p.feed({"runtimeClientError": {}})[0].kind is EventKind.ERROR


class TestFlush:
    def test_unterminated_tool_block_is_emitted_on_flush(self):
        # A stream that ends mid-tool-block must not silently drop the call.
        p = StreamParser()
        p.feed({"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
            "name": "shell", "toolUseId": "tu-7", "input": {"command": "ls"}}}}})
        flushed = p.flush()
        assert flushed and flushed[0].tool_name == "shell"

    def test_flush_is_idempotent(self):
        p = StreamParser()
        p.feed({"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
            "name": "shell", "toolUseId": "tu-8"}}}})
        p.flush()
        assert p.flush() == []



class TestUsage:
    """The event that carries what a turn cost.

    Unparsed, it is indistinguishable from a free model call: every budget
    ceiling downstream reads numbers that stay at zero. So the shapes matter
    more here than anywhere else in this parser.
    """

    def test_usage_nested_under_metadata(self):
        p = StreamParser()
        out = p.feed({"metadata": {
            "usage": {"inputTokens": 48210, "outputTokens": 3120, "totalTokens": 51330},
            "metrics": {"latencyMs": 8140}}})
        assert out[0].kind is EventKind.USAGE
        assert out[0].input_tokens == 48210
        assert out[0].output_tokens == 3120
        assert out[0].latency_ms == 8140

    def test_usage_flattened_onto_metadata(self):
        p = StreamParser()
        out = p.feed({"metadata": {"inputTokens": 100, "outputTokens": 20}})
        assert out[0].kind is EventKind.USAGE
        assert (out[0].input_tokens, out[0].output_tokens) == (100, 20)

    def test_usage_at_the_top_level(self):
        p = StreamParser()
        out = p.feed({"usage": {"inputTokens": 7, "outputTokens": 3}})
        assert out[0].kind is EventKind.USAGE

    def test_snake_case_and_prompt_completion_spellings(self):
        p = StreamParser()
        out = p.feed({"metadata": {"usage": {"prompt_tokens": 12, "completion_tokens": 4}}})
        assert (out[0].input_tokens, out[0].output_tokens) == (12, 4)

    def test_cached_and_reasoning_tokens_are_carried(self):
        p = StreamParser()
        out = p.feed({"metadata": {"usage": {
            "inputTokens": 10, "outputTokens": 2,
            "cacheReadInputTokens": 900, "reasoningTokens": 44}}})
        assert out[0].cached_tokens == 900
        assert out[0].reasoning_tokens == 44

    def test_unreported_cached_tokens_stay_none(self):
        # None and 0 must remain distinguishable: "the provider did not say"
        # is not the same fact as "the provider said none".
        p = StreamParser()
        out = p.feed({"metadata": {"usage": {"inputTokens": 10, "outputTokens": 2}}})
        assert out[0].cached_tokens is None
        assert out[0].reasoning_tokens is None

    def test_counts_arriving_as_strings_are_coerced(self):
        p = StreamParser()
        out = p.feed({"metadata": {"usage": {"inputTokens": "480", "outputTokens": "31.0"}}})
        assert (out[0].input_tokens, out[0].output_tokens) == (480, 31)

    def test_a_reported_zero_is_a_usage_event(self):
        p = StreamParser()
        out = p.feed({"metadata": {"usage": {"inputTokens": 0, "outputTokens": 0}}})
        assert out[0].kind is EventKind.USAGE

    def test_metadata_without_counts_is_not_a_usage_event(self):
        # Falling through to UNKNOWN is right: recording this as usage would
        # mean writing a zero-cost model call that never happened.
        p = StreamParser()
        out = p.feed({"metadata": {"metrics": {"latencyMs": 12}}})
        assert out[0].kind is EventKind.UNKNOWN

    def test_unusable_counts_are_not_a_usage_event(self):
        p = StreamParser()
        out = p.feed({"metadata": {"usage": {"inputTokens": "many", "outputTokens": None}}})
        assert out[0].kind is EventKind.UNKNOWN

    def test_usage_does_not_disturb_text_or_tool_parsing(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "hi"}}},
            {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 1}}},
            {"contentBlockStart": {"contentBlockIndex": 1, "start": {"toolUse": {
                "name": "shell", "toolUseId": "tu-9", "input": {"command": "ls"}}}}},
            {"contentBlockStop": {"contentBlockIndex": 1}},
        ])
        kinds = [e.kind for e in out]
        assert EventKind.TEXT in kinds
        assert EventKind.USAGE in kinds
        assert EventKind.TOOL_USE in kinds



class TestCurrentInvokeHarnessToolInput:
    """The production shape published by AWS and emitted by botocore 1.43.98.

    Start carries only the tool name/id. Each delta carries a string under
    `toolUse.input`; that string is a partial JSON fragment. The old parser
    accepted a dict there and silently ignored the required string, then
    emitted the correctly named tool with `{}`.
    """

    def test_json_string_fragments_under_delta_input_are_accumulated(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockStart": {"contentBlockIndex": 4, "start": {"toolUse": {
                "name": "create_agent", "toolUseId": "tu-prod"}}}},
            {"contentBlockDelta": {"contentBlockIndex": 4, "delta": {"toolUse": {
                "input": '{"name":"Janeisha '}}}},
            {"contentBlockDelta": {"contentBlockIndex": 4, "delta": {"toolUse": {
                "input": 'Carter","role":"Runs strategy","description":"Owns execution"}'}}}},
            {"contentBlockStop": {"contentBlockIndex": 4}},
        ])
        tool = [e for e in out if e.kind is EventKind.TOOL_USE][0]
        assert tool.tool_name == "create_agent"
        assert tool.tool_use_id == "tu-prod"
        assert tool.tool_input == {
            "name": "Janeisha Carter",
            "role": "Runs strategy",
            "description": "Owns execution",
        }
        assert tool.tool_input_observed is True
        assert tool.block_index == 4

    def test_complete_json_string_in_one_delta_is_decoded(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
                "name": "remember", "toolUseId": "tu"}}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
                "input": '{"scope":"agent","body":"prefers bullets"}'}}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
        ])
        assert [e for e in out if e.kind is EventKind.TOOL_USE][0].tool_input \
            == {"scope": "agent", "body": "prefers bullets"}

    def test_empty_string_on_start_does_not_mask_later_fragments(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
                "name": "remember", "toolUseId": "tu", "input": ""}}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
                "input": '{"scope":"agent","body":"x"}'}}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
        ])
        assert [e for e in out if e.kind is EventKind.TOOL_USE][0].tool_input["body"] == "x"

    def test_empty_dict_on_start_does_not_mask_later_fragments(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
                "name": "remember", "toolUseId": "tu", "input": {}}}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
                "input": '{"scope":"agent","body":"x"}'}}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
        ])
        assert [e for e in out if e.kind is EventKind.TOOL_USE][0].tool_input["body"] == "x"

    def test_complete_dict_delta_from_an_older_preview_still_works(self):
        p = StreamParser()
        out = _drain(p, [
            {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
                "name": "remember", "toolUseId": "tu"}}}},
            {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
                "input": {"scope": "agent", "body": "x"}}}}},
            {"contentBlockStop": {"contentBlockIndex": 0}},
        ])
        assert [e for e in out if e.kind is EventKind.TOOL_USE][0].tool_input["body"] == "x"

    def test_partial_json_legacy_aliases_still_work_without_duplication(self):
        for key in ("partial_json", "partialJson"):
            p = StreamParser()
            out = _drain(p, [
                {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
                    "name": "remember", "toolUseId": "tu"}}}},
                {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
                    key: '{"body":"x"}'}}}},
                {"contentBlockStop": {"contentBlockIndex": 0}},
            ])
            assert [e for e in out if e.kind is EventKind.TOOL_USE][0].tool_input == {"body": "x"}

    def test_flush_uses_string_fragments_when_stop_is_missing(self):
        p = StreamParser()
        p.feed({"contentBlockStart": {"contentBlockIndex": 2, "start": {"toolUse": {
            "name": "remember", "toolUseId": "tu"}}}})
        p.feed({"contentBlockDelta": {"contentBlockIndex": 2, "delta": {"toolUse": {
            "input": '{"body":"x"}'}}}})
        tool = p.flush()[0]
        assert tool.tool_input == {"body": "x"} and tool.block_index == 2

    def test_missing_input_is_structurally_distinguishable_from_empty_json(self):
        missing = StreamParser()
        missing.feed({"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
            "name": "create_agent", "toolUseId": "a"}}}})
        empty = StreamParser()
        empty.feed({"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
            "name": "find_agents", "toolUseId": "b"}}}})
        empty.feed({"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
            "input": "{}"}}}})
        assert missing.flush()[0].tool_input_observed is False
        observed = empty.flush()[0]
        assert observed.tool_input_observed is True and observed.tool_input == {}



def test_delta_input_wins_when_a_test_double_sends_every_alias():
    p = StreamParser()
    out = _drain(p, [
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
            "name": "remember", "toolUseId": "tu"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
            "input": '{"body":"once"}',
            "partial_json": '{"body":"duplicated"}',
            "partialJson": '{"body":"duplicated again"}',
        }}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
    ])
    assert [e for e in out if e.kind is EventKind.TOOL_USE][0].tool_input == {"body": "once"}


def test_stop_then_flush_emits_a_tool_exactly_once():
    p = StreamParser()
    events = [
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
            "name": "remember", "toolUseId": "tu"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
            "input": '{"body":"x"}'}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
    ]
    emitted = []
    for event in events:
        emitted.extend(p.feed(event))
    emitted.extend(p.flush())
    assert [e.tool_use_id for e in emitted if e.kind is EventKind.TOOL_USE] == ["tu"]


def test_malformed_current_shape_is_not_silently_emptied():
    p = StreamParser()
    out = _drain(p, [
        {"contentBlockStart": {"contentBlockIndex": 0, "start": {"toolUse": {
            "name": "create_agent", "toolUseId": "tu"}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {
            "input": '{"name":"Janeisha"'}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
    ])
    tool = [e for e in out if e.kind is EventKind.TOOL_USE][0]
    assert tool.tool_input != {}
    assert tool.tool_input == {"_unparsed": '{"name":"Janeisha"'}
