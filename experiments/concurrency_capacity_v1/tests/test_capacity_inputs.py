import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import capacity_inputs as ci  # noqa: E402


def base_rows(count=4096, first_index=33):
    return [dict(request_index=str(first_index + i), phase='measurement',
                 bin=str(i // 128 + 1), round='0', key=f'key{i % 512}',
                 requester=str(i % 32 + 1), owner=str(i % 16 + 1),
                 value_size_bytes='65536', expected_fnv1a64=f'{i:016x}',
                 schedule_sha256='schedulehash') for i in range(count)]


class ExpansionTest(unittest.TestCase):
    def test_single_pass_is_copy(self):
        rows = base_rows(8)
        expanded = ci.expand_measurement(rows, 1)
        self.assertEqual(expanded, rows)
        self.assertIsNot(expanded[0], rows[0])
        self.assertTrue(ci.validate_expansion(rows, expanded, 1))

    def test_repeats_are_unique_and_identity_preserving(self):
        rows = base_rows(256)
        for repeats in (2, 4, 8):
            expanded = ci.expand_measurement(rows, repeats)
            self.assertEqual(len(expanded), len(rows) * repeats)
            self.assertEqual(len({r['request_index'] for r in expanded}), len(expanded))
            self.assertTrue(ci.validate_expansion(rows, expanded, repeats))

    def test_relabelled_fields_only(self):
        rows = base_rows(4)
        expanded = ci.expand_measurement(rows, 2)
        for position, row in enumerate(expanded):
            original = rows[position % len(rows)]
            for field in ci.IDENTITY_FIELDS:
                self.assertEqual(row[field], original[field], field)
        self.assertNotEqual(expanded[0]['request_index'], expanded[4]['request_index'])
        self.assertEqual(int(expanded[4]['bin']), int(rows[0]['bin']) + ci._max_int(rows, 'bin'))

    def test_rejects_bad_repeats(self):
        with self.assertRaises(ValueError):
            ci.expand_measurement(base_rows(2), 0)

    def test_validate_detects_mutation(self):
        rows = base_rows(4)
        expanded = ci.expand_measurement(rows, 2)
        expanded[0]['key'] = 'tampered'
        with self.assertRaises(AssertionError):
            ci.validate_expansion(rows, expanded, 2)


if __name__ == '__main__':
    unittest.main()