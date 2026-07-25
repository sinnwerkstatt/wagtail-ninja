# table block
# columns = None
# col_types = []
# content_types = []
# for block_name, block_type in block.child_blocks.items():
#     # ColTypedDict = TypedDict(f"{block_name}Column", {"type": Literal[block_name], "heading": str})
#     # if not columns:
#     #     columns = ColTypedDict
#     #     # columns = TypedDict(f"{block.__class__.__name__}Columns", {"type": Literal[block_name], "heading": str})
#     # else:
#     #     columns |= ColTypedDict
#     col_types.append(block_name)
#     content_types.append(_wagtail_block_map(block_type, block_name))
# inner_block_types = block.child_blocks
# print("MNUSS", inner_block_types)
# print(col_types, content_types)
# class MyTypeColumn(TypedDict):
#     type: str
#     heading: str
# MyTypeColumn = TypedDict(
#     f"{block.__class__.__name__}Column",
#     {"type": Literal[[Literal[x] for x in col_types]], "heading": str},
# )

# class TypedTableColumn(TypedDict):
#     type: str
#     heading: str
#
# class TypedTableRow(TypedDict):
#     values: list[Any]
#
# return TypedDict(
#     f"{block.__class__.__name__}Value",
#     {
#         "caption": str,
#         "columns": list[TypedTableColumn],
#         "rows": list[TypedTableRow],
#     },
# )
# return TypedDict(
#     f"{block.__class__.__name__}Value",
#     {
#         "caption": str,
#         "columns": list[
#             TypedDict("TypedTableColumn", {"type": str, "heading": str})
#         ],
#         "rows": list[TypedDict("TypedTableRow", {"values": list[Any]})],
#     },
# )
