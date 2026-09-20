import unittest

from ..hashing import canonical_json, event_hash, sha256_hex


class HashingTests(unittest.TestCase):
    def test_canonical_json_order_independent(self):
        a = canonical_json({"b": 1, "a": 2})
        b = canonical_json({"a": 2, "b": 1})
        self.assertEqual(a, b)
        self.assertEqual(a, '{"a":2,"b":1}')

    def test_canonical_json_rejects_unserializable(self):
        class NotSerializable:
            pass

        with self.assertRaises(TypeError):
            canonical_json({"x": NotSerializable()})

    def test_sha256_hex_vectors(self):
        self.assertEqual(
            sha256_hex(""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertEqual(
            sha256_hex("abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )
        self.assertEqual(
            sha256_hex("é日本"),
            sha256_hex("é日本".encode("utf-8")),
        )

    def test_event_hash_stable_across_key_order(self):
        payload_a = {"type": "TicketCreated", "id": "TESS-1", "fields": {"status": "open", "priority": "high"}}
        payload_b = {"fields": {"priority": "high", "status": "open"}, "id": "TESS-1", "type": "TicketCreated"}
        self.assertEqual(event_hash(payload_a), event_hash(payload_b))


if __name__ == "__main__":
    unittest.main()
