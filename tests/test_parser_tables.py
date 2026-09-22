from support_doc_extractor.parsers import table_to_rows, walk_tables


def test_opendataloader_nested_row_cells_are_preserved():
    table = {
        "type": "table",
        "rows": [
            {"type": "row", "cells": [
                {"type": "cell", "kids": [{"type": "paragraph", "content": "项目名称"}]},
                {"type": "cell", "kids": [{"type": "paragraph", "content": "示例风电项目"}]},
            ]},
            {"type": "row", "cells": [
                {"type": "cell", "content": "建设单位"},
                {"type": "cell", "content": "示例新能源有限公司"},
            ]},
        ],
    }
    assert table_to_rows(table) == [
        ["项目名称", "示例风电项目"],
        ["建设单位", "示例新能源有限公司"],
    ]


def test_walk_tables_does_not_treat_rows_as_tables():
    table = {"type": "table", "rows": [{"type": "row", "cells": [{"type": "cell", "content": "A"}]}]}
    assert list(walk_tables(table)) == [table]
