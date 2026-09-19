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
