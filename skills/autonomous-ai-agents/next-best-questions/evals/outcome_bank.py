"""outcome_bank.py — ambiguous-but-executable tasks for the objective-outcome eval (P3-P6).

The ClarifyGPT/AmbigSWE recipe: each task states an AMBIGUOUS prompt whose discriminating
details live in `hidden_spec` (what a real user would know but didn't say), pinned by
executable `tests`. Every task is hand-checked so that (a) the ambiguity is REAL — at least
two materially different implementations are plausible readings of the prompt alone — and
(b) the tests pin exactly one. Tasks are self-contained pure Python (no imports needed by
the solution beyond the stdlib; the runner execs solution+tests in a subprocess).

Fields: id · category · ambiguous_prompt · hidden_spec · func (entry point) · tests (list of
assert-expression strings, each evaluated independently) · ambiguity (the ≥2 plausible
readings, documented for the fixture test).
"""

TASKS = [
    {
        "id": "sort-words",
        "category": "strings",
        "ambiguous_prompt": "Write a Python function sort_words(words) that sorts a list of words.",
        "hidden_spec": "Sort case-insensitively, ascending; keep each word's original casing; "
                       "equal keys keep their original relative order (stable).",
        "func": "sort_words",
        "tests": [
            "sort_words(['Banana', 'apple', 'Cherry']) == ['apple', 'Banana', 'Cherry']",
            "sort_words(['b', 'A', 'B', 'a']) == ['A', 'a', 'b', 'B']",
            "sort_words([]) == []",
        ],
        "ambiguity": ["case-sensitive ASCII sort (uppercase first)",
                      "case-insensitive sort keeping original casing"],
    },
    {
        "id": "round-price",
        "category": "numbers",
        "ambiguous_prompt": "Write a Python function round_price(x) that rounds a price to two decimals.",
        "hidden_spec": "Round HALF UP (0.005 always rounds up), not banker's rounding; return a float.",
        "func": "round_price",
        "tests": [
            "round_price(2.675) == 2.68",
            "round_price(0.125) == 0.13",
            "round_price(1.0) == 1.0",
        ],
        "ambiguity": ["built-in round() (banker's: 2.675 -> 2.67 via float repr)",
                      "decimal half-up (2.675 -> 2.68)"],
    },
    {
        "id": "dedupe",
        "category": "lists",
        "ambiguous_prompt": "Write a Python function dedupe(items) that removes duplicates from a list.",
        "hidden_spec": "Preserve first-occurrence order; items are hashable; return a new list.",
        "func": "dedupe",
        "tests": [
            "dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]",
            "dedupe(['b', 'a', 'b']) == ['b', 'a']",
            "dedupe([]) == []",
        ],
        "ambiguity": ["set()-based (order lost)", "order-preserving first occurrence"],
    },
    {
        "id": "parse-name",
        "category": "strings",
        "ambiguous_prompt": "Write a Python function parse_name(s) that splits a full name into "
                            "first and last name.",
        "hidden_spec": "Return a (first, last) tuple; the FIRST whitespace token is the first name, "
                       "everything else (joined by single spaces) is the last name; a single token "
                       "returns (token, '').",
        "func": "parse_name",
        "tests": [
            "parse_name('Mary Ann Smith') == ('Mary', 'Ann Smith')",
            "parse_name('Cher') == ('Cher', '')",
            "parse_name('Jean  Claude   Van Damme') == ('Jean', 'Claude Van Damme')",
        ],
        "ambiguity": ["last token = last name, middle names dropped/joined to first",
                      "first token = first name, rest = last name"],
    },
    {
        "id": "fmt-date",
        "category": "dates",
        "ambiguous_prompt": "Write a Python function fmt_date(d) that formats a date string for display.",
        "hidden_spec": "Input is ISO 'YYYY-MM-DD'; output is 'DD/MM/YYYY' (day first, slashes, "
                       "zero-padded).",
        "func": "fmt_date",
        "tests": [
            "fmt_date('2026-07-03') == '03/07/2026'",
            "fmt_date('1999-12-31') == '31/12/1999'",
        ],
        "ambiguity": ["US 'MM/DD/YYYY'", "'DD/MM/YYYY'", "long form 'July 3, 2026'"],
    },
    {
        "id": "truncate",
        "category": "strings",
        "ambiguous_prompt": "Write a Python function truncate(s, n) that shortens text to fit "
                            "within n characters.",
        "hidden_spec": "If s fits (len(s) <= n) return it unchanged; otherwise cut and append a "
                       "single '…' (U+2026) so the TOTAL length is exactly n.",
        "func": "truncate",
        "tests": [
            "truncate('hello world', 8) == 'hello w…'",
            "truncate('hi', 8) == 'hi'",
            "truncate('abcdef', 6) == 'abcdef'",
        ],
        "ambiguity": ["hard cut, no ellipsis", "'...' three chars", "ellipsis counted outside n"],
    },
    {
        "id": "pct",
        "category": "numbers",
        "ambiguous_prompt": "Write a Python function pct(part, whole) that returns a percentage.",
        "hidden_spec": "Return part/whole*100 rounded to 1 decimal as a float; whole == 0 "
                       "returns 0.0 (never raises).",
        "func": "pct",
        "tests": [
            "pct(1, 3) == 33.3",
            "pct(5, 0) == 0.0",
            "pct(2, 4) == 50.0",
        ],
        "ambiguity": ["0-1 fraction vs 0-100", "unrounded float", "ZeroDivisionError on whole=0"],
    },
    {
        "id": "flatten",
        "category": "lists",
        "ambiguous_prompt": "Write a Python function flatten(xs) that flattens a nested list.",
        "hidden_spec": "Flatten exactly ONE level; deeper nesting is preserved as-is.",
        "func": "flatten",
        "tests": [
            "flatten([[1, 2], [3], [4, [5]]]) == [1, 2, 3, 4, [5]]",
            "flatten([]) == []",
            "flatten([[1], [], [2]]) == [1, 2]",
        ],
        "ambiguity": ["fully recursive flatten", "one-level flatten"],
    },
    {
        "id": "count-words",
        "category": "strings",
        "ambiguous_prompt": "Write a Python function count_words(text) that counts the words "
                            "in a text.",
        "hidden_spec": "Return a dict of word -> count; split on whitespace; compare "
                       "case-insensitively (keys are lowercase); no punctuation stripping.",
        "func": "count_words",
        "tests": [
            "count_words('a A b') == {'a': 2, 'b': 1}",
            "count_words('') == {}",
            "count_words('Dog dog DOG cat') == {'dog': 3, 'cat': 1}",
        ],
        "ambiguity": ["return total int count", "case-sensitive keys", "strip punctuation"],
    },
    {
        "id": "slugify",
        "category": "strings",
        "ambiguous_prompt": "Write a Python function slugify(title) that makes a URL slug "
                            "from a title.",
        "hidden_spec": "Lowercase; every maximal run of non-alphanumeric characters becomes ONE "
                       "hyphen; no leading/trailing hyphens.",
        "func": "slugify",
        "tests": [
            "slugify(' Hello,  World! ') == 'hello-world'",
            "slugify('a--b') == 'a-b'",
            "slugify('Already-Fine') == 'already-fine'",
        ],
        "ambiguity": ["underscores vs hyphens", "strip non-alnum entirely", "keep leading hyphen"],
    },
    {
        "id": "median",
        "category": "numbers",
        "ambiguous_prompt": "Write a Python function median(xs) that returns the median of a "
                            "list of numbers.",
        "hidden_spec": "Sort a copy (don't mutate); odd n -> middle element; even n -> the MEAN of "
                       "the two middle elements as a float.",
        "func": "median",
        "tests": [
            "median([1, 2, 3, 4]) == 2.5",
            "median([3, 1, 2]) == 2",
            "(lambda xs: (median(xs), xs)[1])([2, 1]) == [2, 1]",
        ],
        "ambiguity": ["even n -> lower middle element", "even n -> mean of middles"],
    },
    {
        "id": "normalize-scores",
        "category": "numbers",
        "ambiguous_prompt": "Write a Python function normalize_scores(xs) that prepares raw "
                            "scores for display.",
        "hidden_spec": "Round each to the nearest integer (Python round), then clamp into "
                       "[0, 100]; return a list of ints.",
        "func": "normalize_scores",
        "tests": [
            "normalize_scores([-5, 50.4, 120]) == [0, 50, 100]",
            "normalize_scores([99.6]) == [100]",
            "normalize_scores([]) == []",
        ],
        "ambiguity": ["rescale min-max to 0-100", "clamp only, keep floats", "round+clamp ints"],
    },
    {
        "id": "initials",
        "category": "strings",
        "ambiguous_prompt": "Write a Python function initials(name) that returns a person's "
                            "initials.",
        "hidden_spec": "Use ONLY the first and last whitespace tokens; two uppercase letters, "
                       "no separators; a single token returns its single uppercase initial.",
        "func": "initials",
        "tests": [
            "initials('mary ann smith') == 'MS'",
            "initials('cher') == 'C'",
            "initials('Jean Claude van damme') == 'JD'",
        ],
        "ambiguity": ["one initial per token ('MAS')", "dots between ('M.S.')", "first+last only"],
    },
    {
        "id": "next-id",
        "category": "strings",
        "ambiguous_prompt": "Write a Python function next_id(ids) that generates the next id "
                            "for a new item.",
        "hidden_spec": "ids are strings like 'item-7'; return 'item-<max numeric suffix + 1>'; "
                       "an empty list returns 'item-1'.",
        "func": "next_id",
        "tests": [
            "next_id(['item-1', 'item-7', 'item-3']) == 'item-8'",
            "next_id([]) == 'item-1'",
            "next_id(['item-9', 'item-10']) == 'item-11'",
        ],
        "ambiguity": ["len(ids)+1", "max+1 with lexicographic max bug ('item-9' > 'item-10')"],
    },
    {
        "id": "adults",
        "category": "dicts",
        "ambiguous_prompt": "Write a Python function adults(people) that filters a list of "
                            "people down to the adults.",
        "hidden_spec": "people is a list of dicts with an 'age' key; keep age >= 18 (18 counts as "
                       "adult); preserve order; return a new list of the same dicts.",
        "func": "adults",
        "tests": [
            "adults([{'age': 17}, {'age': 18}]) == [{'age': 18}]",
            "adults([]) == []",
            "adults([{'age': 21}, {'age': 2}]) == [{'age': 21}]",
        ],
        "ambiguity": ["age > 18 exclusive", "age >= 18 inclusive", "age >= 21"],
    },
    {
        "id": "merge-config",
        "category": "dicts",
        "ambiguous_prompt": "Write a Python function merge(a, b) that merges two configuration "
                            "dicts.",
        "hidden_spec": "Return a NEW dict; on key conflicts b wins; non-recursive (nested dicts "
                       "replaced whole); neither input is mutated.",
        "func": "merge",
        "tests": [
            "merge({'x': 1, 'y': 1}, {'y': 2}) == {'x': 1, 'y': 2}",
            "merge({'n': {'a': 1}}, {'n': {'b': 2}}) == {'n': {'b': 2}}",
            "(lambda a: (merge(a, {'k': 2}), a)[1])({'k': 1}) == {'k': 1}",
        ],
        "ambiguity": ["a wins on conflict", "deep/recursive merge", "in-place mutation of a"],
    },
    {
        "id": "to-seconds",
        "category": "dates",
        "ambiguous_prompt": "Write a Python function to_seconds(s) that parses a duration string "
                            "into seconds.",
        "hidden_spec": "Format is 'MM:SS' (minutes may exceed 59); return an int.",
        "func": "to_seconds",
        "tests": [
            "to_seconds('02:30') == 150",
            "to_seconds('90:00') == 5400",
            "to_seconds('00:07') == 7",
        ],
        "ambiguity": ["'HH:MM:SS'", "'MM:SS'", "'1h30m' style tokens"],
    },
    {
        "id": "fmt-money",
        "category": "numbers",
        "ambiguous_prompt": "Write a Python function fmt_money(cents) that formats a money "
                            "amount for display.",
        "hidden_spec": "Input is an int number of cents; output like '$12.34'; negative amounts "
                       "format as '-$0.50' (sign before the dollar sign); always two decimals.",
        "func": "fmt_money",
        "tests": [
            "fmt_money(1234) == '$12.34'",
            "fmt_money(-50) == '-$0.50'",
            "fmt_money(0) == '$0.00'",
        ],
        "ambiguity": ["input is float dollars", "'$-0.50' sign placement", "thousands separators"],
    },
    {
        "id": "chunk",
        "category": "lists",
        "ambiguous_prompt": "Write a Python function chunk(xs, n) that splits a list into chunks.",
        "hidden_spec": "Chunks of size n in order; the LAST chunk may be shorter (no padding); "
                       "n >= 1 may be assumed.",
        "func": "chunk",
        "tests": [
            "chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]",
            "chunk([], 3) == []",
            "chunk([1, 2], 5) == [[1, 2]]",
        ],
        "ambiguity": ["pad the last chunk with None", "drop a short last chunk", "n chunks vs size-n"],
    },
    {
        "id": "is-weekend",
        "category": "dates",
        "ambiguous_prompt": "Write a Python function is_weekend(day) that checks whether a day "
                            "is on the weekend.",
        "hidden_spec": "day is a day-NAME string, case-insensitive ('saturday'/'Sunday'/...); "
                       "weekend = Saturday or Sunday; return a bool.",
        "func": "is_weekend",
        "tests": [
            "is_weekend('saturday') is True",
            "is_weekend('SUNDAY') is True",
            "is_weekend('Monday') is False",
        ],
        "ambiguity": ["day is a datetime.date", "day is a 0-6 int (which day is 0?)",
                      "Fri/Sat weekend (some locales)"],
    },
]

BY_ID = {t["id"]: t for t in TASKS}
