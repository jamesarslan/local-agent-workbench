"""Chat-template compatibility: GGUF header reading and the late-system-message fix."""

import struct
import tempfile
import unittest
from pathlib import Path

import chat_templates as ct

QWEN_LIKE = """{%- for message in messages %}
    {%- set content = message.content|trim %}
    {%- if message.role == "system" %}
        {%- if not loop.first %}
            {{- raise_exception('System message must be at the beginning.') }}
        {%- endif %}
    {%- elif message.role == "user" %}
        {{- '<|im_start|>' + message.role + '\\n' + content + '<|im_end|>' + '\\n' }}
    {%- endif %}
{%- endfor %}"""
TOLERANT = "{%- for m in messages %}{{ '<|start|>' + m.role + m.content }}{%- endfor %}"


def text(value):
    raw = value.encode("utf8")
    return struct.pack("<Q", len(raw)) + raw


def gguf(template, version=3, extra=b""):
    """A minimal GGUF header: an architecture string, a token array, a number array, the template."""
    kvs = [
        text("general.architecture") + struct.pack("<I", 8) + text("qwen"),
        text("tokenizer.ggml.tokens") + struct.pack("<IIQ", 9, 8, 3) + text("a") + text("bb") + text("ccc"),
        text("tokenizer.ggml.scores") + struct.pack("<IIQ", 9, 6, 3) + struct.pack("<3f", 1, 2, 3),
        text("general.file_type") + struct.pack("<I", 4) + struct.pack("<I", 15),
    ]
    if template is not None:
        kvs.append(text(ct.KEY) + struct.pack("<I", 8) + text(template))
    return b"GGUF" + struct.pack("<IQQ", version, 0, len(kvs)) + b"".join(kvs) + extra


class ChatTemplateTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_reads_template_after_skipping_arrays_and_numbers(self):
        path = self.write("m.gguf", gguf(QWEN_LIKE))
        self.assertEqual(ct.embedded(path), QWEN_LIKE)

    def test_suffix_does_not_matter(self):
        path = self.write("m.gguf_file", gguf(QWEN_LIKE))
        self.assertEqual(ct.embedded(path), QWEN_LIKE)

    def test_missing_or_damaged_files_give_none(self):
        whole = gguf(QWEN_LIKE)
        cases = {
            "no-template.gguf": gguf(None),
            "not-gguf.bin": b"NOPE" + whole[4:],
            "truncated.gguf": whole[: len(whole) - 10],
            "empty.gguf": b"",
            "version1.gguf": gguf(QWEN_LIKE, version=1),
            "huge-key.gguf": b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + struct.pack("<Q", 1 << 40),
            "bad-kind.gguf": b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + text("k") + struct.pack("<I", 99),
        }
        for name, data in cases.items():
            with self.subTest(name=name):
                self.assertIsNone(ct.embedded(self.write(name, data)))
        self.assertIsNone(ct.embedded(self.dir / "absent.gguf"))
        self.assertIsNone(ct.embedded(self.dir))

    def test_fix_renders_a_late_system_message_as_a_chatml_turn(self):
        fixed = ct.late_system_fix(QWEN_LIKE)
        self.assertNotIn("raise_exception", fixed)
        self.assertIn("'<|im_start|>system\\n' + content + '<|im_end|>\\n'", fixed)
        self.assertEqual(fixed.replace(ct.CHATML_TURN, ct.LATE_SYSTEM), QWEN_LIKE)

    def test_fix_leaves_other_templates_alone(self):
        for template in (None, "", TOLERANT, QWEN_LIKE.replace("<|im_start|>", "<|turn|>"),
                         QWEN_LIKE + QWEN_LIKE, QWEN_LIKE.replace("set content", "set body")):
            with self.subTest(template=(template or "")[:30]):
                self.assertIsNone(ct.late_system_fix(template))

    def test_override_writes_once_and_reuses_the_file(self):
        model = self.write("m.gguf", gguf(QWEN_LIKE))
        folder = self.dir / "templates"
        first = ct.override(model, folder)
        self.assertEqual(first.read_text(encoding="utf8"), ct.late_system_fix(QWEN_LIKE))
        stamp = first.stat().st_mtime_ns
        self.assertEqual(ct.override(model, folder), first)
        self.assertEqual(first.stat().st_mtime_ns, stamp)
        self.assertEqual([p.name for p in folder.iterdir()], [first.name])

    def test_override_is_none_when_no_fix_applies(self):
        folder = self.dir / "templates"
        self.assertIsNone(ct.override(self.write("t.gguf", gguf(TOLERANT)), folder))
        self.assertIsNone(ct.override(self.write("w.gguf", b"weights"), folder))
        self.assertFalse(folder.exists())


if __name__ == "__main__":
    unittest.main()
