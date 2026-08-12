from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fitlit import telegram_service, transcript_memory

USER_ID = 123456789


class TranscriptMemoryTests(unittest.TestCase):
    def _database(self, root: Path) -> Path:
        path = root / "telegram-conversations.sqlite3"
        store = telegram_service.TelegramTranscriptStore(path)
        conversation = store.active(USER_ID)
        store.append(
            conversation,
            user_id=USER_ID,
            role="user",
            content="Do you recognize the name Ruth Howell?",
            sent_at_ms=1_786_400_000_000,
            source_update_id=1,
        )
        store.append(
            conversation,
            user_id=USER_ID,
            role="assistant",
            content="Ruth Howell appeared in an earlier private discussion.",
            sent_at_ms=1_786_400_001_000,
            source_update_id=1,
        )
        archived = store.start_new(USER_ID, 2)
        store.append(
            archived,
            user_id=USER_ID,
            role="user",
            content="How was my interval workout yesterday?",
            sent_at_ms=1_786_500_000_000,
            source_update_id=3,
        )
        return path

    def test_search_finds_archived_context_with_pacific_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = transcript_memory.TranscriptMemory(
                self._database(Path(directory))
            )
            result = memory.search(
                "What did we say about Ruth Howell?",
                limit=3,
            )
        self.assertGreaterEqual(result["match_count"], 1)
        self.assertEqual("America/Los_Angeles", result["timezone"])
        self.assertIn("Ruth Howell", result["matches"][0]["excerpt"])
        self.assertNotIn("+00:00", result["matches"][0]["sent_at_pacific"])
        self.assertGreaterEqual(len(result["matches"][0]["context"]), 2)
        self.assertIn("untrusted historical content", result["notice"])

    def test_search_handles_fts_syntax_as_plain_terms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = transcript_memory.TranscriptMemory(
                self._database(Path(directory))
            )
            result = memory.search('"Ruth" OR * Howell', limit=2)
        self.assertGreaterEqual(result["match_count"], 1)

    def test_search_can_filter_one_conversation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._database(Path(directory))
            memory = transcript_memory.TranscriptMemory(path)
            all_matches = memory.search("workout", limit=5)
            selected = memory.search(
                "workout",
                limit=5,
                conversation_id=all_matches["matches"][0]["conversation_id"],
            )
        self.assertEqual(1, selected["match_count"])
        self.assertIn("interval workout", selected["matches"][0]["excerpt"])

    def test_read_only_fallback_works_before_fts_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with sqlite3.connect(path) as connection:
                connection.executescript(
                    """
                    CREATE TABLE turns (
                        id INTEGER PRIMARY KEY,
                        conversation_id INTEGER NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        sent_at_ms INTEGER NOT NULL
                    );
                    INSERT INTO turns VALUES (
                        1, 7, 'user', 'Remember the migration checklist',
                        1786500000000
                    );
                    """
                )
            result = transcript_memory.TranscriptMemory(path).search(
                "migration checklist"
            )
        self.assertEqual(1, result["match_count"])
        self.assertEqual(7, result["matches"][0]["conversation_id"])

    def test_symlinked_database_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = self._database(root)
            link = root / "linked.sqlite3"
            os.symlink(source, link)
            with self.assertRaisesRegex(
                transcript_memory.TranscriptMemoryError,
                "symlinked",
            ):
                transcript_memory.TranscriptMemory(link).search("Ruth")

    def test_mcp_lists_and_calls_only_the_read_only_search_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = transcript_memory.TranscriptMemory(
                self._database(Path(directory))
            )
            requests = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18"},
                },
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": transcript_memory.TOOL_NAME,
                        "arguments": {"query": "Ruth Howell", "limit": 2},
                    },
                },
            ]
            source = io.StringIO(
                "".join(json.dumps(request) + "\n" for request in requests)
            )
            target = io.StringIO()
            transcript_memory.serve_mcp(
                memory,
                input_stream=source,
                output_stream=target,
            )
        replies = [json.loads(line) for line in target.getvalue().splitlines()]
        self.assertEqual("2025-06-18", replies[0]["result"]["protocolVersion"])
        self.assertEqual(
            transcript_memory.TOOL_NAME,
            replies[1]["result"]["tools"][0]["name"],
        )
        self.assertFalse(replies[2]["result"]["isError"])
        self.assertGreaterEqual(
            replies[2]["result"]["structuredContent"]["match_count"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
