#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t merlo_allocations = 0;
static uint64_t merlo_frees = 0;
static uint64_t merlo_text_allocations = 0;
static uint64_t merlo_text_frees = 0;
static uint64_t merlo_vec_allocations = 0;
static uint64_t merlo_vec_frees = 0;
static uint64_t merlo_vec_reallocations = 0;
static uint64_t merlo_vec_growths = 0;
static uint64_t merlo_vec_initialized = 0;
static uint64_t merlo_vec_elements_dropped = 0;
static uint64_t merlo_box_allocations = 0;
static uint64_t merlo_box_frees = 0;
static uint64_t merlo_ast_nodes_allocated = 0;
static uint64_t merlo_ast_nodes_freed = 0;
static uint64_t merlo_bytes_copied = 0;
static uint64_t merlo_drop_calls = 0;

static void merlo_overflow_trap(const char *message) {
    fprintf(stderr, "MerloOverflow:%s\n", message);
    abort();
}

static void merlo_allocation_trap(void) {
    fputs("MerloAllocationFailure\n", stderr);
    abort();
}

static void merlo_bounds_trap(uint64_t index, uint64_t length) {
    fprintf(stderr, "MerloBounds:%" PRIu64 ":%" PRIu64 "\n", index, length);
    abort();
}

static void merlo_ownership_trap(const char *message) {
    fprintf(stderr, "MerloOwnership:%s\n", message);
    abort();
}

typedef uint32_t Merlo_ErrorKind;
typedef struct Merlo_Json Merlo_Json;
typedef struct Merlo_JsonField Merlo_JsonField;
typedef struct Merlo_Parser Merlo_Parser;
typedef struct Merlo_ProgramResult Merlo_ProgramResult;
typedef struct Merlo_Stats Merlo_Stats;
typedef struct Merlo_Token Merlo_Token;
typedef uint32_t Merlo_TokenKind;

typedef struct { uint8_t *data; uint64_t length; } MerloBytes;
typedef struct { const uint8_t *data; uint64_t length; } MerloBytesView;
typedef struct { const uint8_t *data; uint64_t length; } MerloTextView;
typedef struct { uint8_t *data; uint64_t length; } MerloText;
typedef struct { uint8_t *data; uint64_t length; uint64_t capacity; } MerloTextBuilder;

typedef struct { uint64_t *data; } MerloBox_UInt64;
typedef struct { Merlo_JsonField *data; uint64_t length; uint64_t capacity; uint64_t active_views; } MerloVec_JsonField;
typedef struct { MerloVec_JsonField *owner; uint64_t generation; } MerloVec_JsonFieldView;
typedef struct { Merlo_Json *data; uint64_t length; uint64_t capacity; uint64_t active_views; } MerloVec_Json;
typedef struct { MerloVec_Json *owner; uint64_t generation; } MerloVec_JsonView;
typedef struct { Merlo_Token *data; uint64_t length; uint64_t capacity; uint64_t active_views; } MerloVec_Token;
typedef struct { MerloVec_Token *owner; uint64_t generation; } MerloVec_TokenView;

static const Merlo_ErrorKind MERLO_ErrorKind_NoError = UINT32_C(0);
static const Merlo_ErrorKind MERLO_ErrorKind_InvalidUtf8 = UINT32_C(1);
static const Merlo_ErrorKind MERLO_ErrorKind_UnexpectedByte = UINT32_C(2);
static const Merlo_ErrorKind MERLO_ErrorKind_InvalidLiteral = UINT32_C(3);
static const Merlo_ErrorKind MERLO_ErrorKind_InvalidNumber = UINT32_C(4);
static const Merlo_ErrorKind MERLO_ErrorKind_InvalidString = UINT32_C(5);
static const Merlo_ErrorKind MERLO_ErrorKind_InvalidEscape = UINT32_C(6);
static const Merlo_ErrorKind MERLO_ErrorKind_UnexpectedToken = UINT32_C(7);
static const Merlo_ErrorKind MERLO_ErrorKind_UnexpectedEnd = UINT32_C(8);
static const Merlo_ErrorKind MERLO_ErrorKind_ExpectedColon = UINT32_C(9);
static const Merlo_ErrorKind MERLO_ErrorKind_ExpectedCommaOrEnd = UINT32_C(10);
static const Merlo_ErrorKind MERLO_ErrorKind_DepthExceeded = UINT32_C(11);
static const Merlo_ErrorKind MERLO_ErrorKind_TrailingTokens = UINT32_C(12);
struct Merlo_Json {
    uint32_t tag;
    union {
        bool Bool;
        MerloText Number;
        MerloText String;
        MerloVec_Json Array;
        MerloVec_JsonField Object;
    } payload;
};
#define MERLO_Json_Null_TAG UINT32_C(0)
#define MERLO_Json_Bool_TAG UINT32_C(1)
#define MERLO_Json_Number_TAG UINT32_C(2)
#define MERLO_Json_String_TAG UINT32_C(3)
#define MERLO_Json_Array_TAG UINT32_C(4)
#define MERLO_Json_Object_TAG UINT32_C(5)
#define MERLO_Json_MOVED_TAG UINT32_MAX
static const Merlo_TokenKind MERLO_TokenKind_LBrace = UINT32_C(0);
static const Merlo_TokenKind MERLO_TokenKind_RBrace = UINT32_C(1);
static const Merlo_TokenKind MERLO_TokenKind_LBracket = UINT32_C(2);
static const Merlo_TokenKind MERLO_TokenKind_RBracket = UINT32_C(3);
static const Merlo_TokenKind MERLO_TokenKind_Colon = UINT32_C(4);
static const Merlo_TokenKind MERLO_TokenKind_Comma = UINT32_C(5);
static const Merlo_TokenKind MERLO_TokenKind_Null = UINT32_C(6);
static const Merlo_TokenKind MERLO_TokenKind_TrueLiteral = UINT32_C(7);
static const Merlo_TokenKind MERLO_TokenKind_FalseLiteral = UINT32_C(8);
static const Merlo_TokenKind MERLO_TokenKind_Number = UINT32_C(9);
static const Merlo_TokenKind MERLO_TokenKind_String = UINT32_C(10);
static const Merlo_TokenKind MERLO_TokenKind_End = UINT32_C(11);
struct Merlo_JsonField {
    MerloText key;
    Merlo_Json value;
};
struct Merlo_Parser {
    MerloBytesView input;
    MerloVec_Token tokens;
    uint64_t index;
    Merlo_ErrorKind error;
    uint64_t error_offset;
};
struct Merlo_ProgramResult {
    bool ok;
    Merlo_ErrorKind error;
    uint64_t error_offset;
    uint64_t nodes;
    uint64_t arrays;
    uint64_t objects;
    uint64_t fields;
    uint64_t checksum;
};
struct Merlo_Stats {
    uint64_t nodes;
    uint64_t nulls;
    uint64_t bools;
    uint64_t numbers;
    uint64_t strings;
    uint64_t arrays;
    uint64_t objects;
    uint64_t fields;
    uint64_t checksum;
};
struct Merlo_Token {
    Merlo_TokenKind kind;
    uint64_t start;
    uint64_t end;
    bool escaped;
};

static void merlo_fn_set_error(Merlo_Parser *parser, Merlo_ErrorKind kind, uint64_t offset);
static bool merlo_fn_is_space(uint64_t byte);
static bool merlo_fn_is_digit(uint64_t byte);
static bool merlo_fn_is_digit_one_to_nine(uint64_t byte);
static uint64_t merlo_fn_hex_value(uint64_t byte);
static uint64_t merlo_fn_box_smoke(uint64_t value);
static bool merlo_fn_validate_utf8(Merlo_Parser *parser);
static void merlo_fn_push_token(Merlo_Parser *parser, Merlo_TokenKind kind, uint64_t start, uint64_t end, bool escaped);
static uint64_t merlo_fn_scan_string(Merlo_Parser *parser, uint64_t quote);
static uint64_t merlo_fn_scan_number(Merlo_Parser *parser, uint64_t start);
static uint64_t merlo_fn_match_literal(Merlo_Parser *parser, uint64_t start, uint64_t a, uint64_t b, uint64_t c, uint64_t d, uint64_t e, uint64_t length, Merlo_TokenKind kind);
static void merlo_fn_tokenize(Merlo_Parser *parser);
static Merlo_Token merlo_fn_token_at(Merlo_Parser *parser);
static Merlo_Token merlo_fn_advance(Merlo_Parser *parser);
static MerloText merlo_fn_decode_string(Merlo_Parser *parser, Merlo_Token *token);
static Merlo_Json merlo_fn_parse_array(Merlo_Parser *parser, uint64_t depth);
static Merlo_Json merlo_fn_parse_object(Merlo_Parser *parser, uint64_t depth);
static Merlo_Json merlo_fn_parse_value(Merlo_Parser *parser, uint64_t depth);
static void merlo_fn_checksum_byte(Merlo_Stats *stats, uint64_t byte);
static void merlo_fn_checksum_text(Merlo_Stats *stats, MerloText *text);
static void merlo_fn_visit_json(Merlo_Json *value, Merlo_Stats *stats);
static Merlo_ProgramResult merlo_fn_main(MerloBytesView data);

static uint8_t merlo_bytes_load(const MerloBytesView *view, uint64_t index) {
    if (index >= view->length) merlo_bounds_trap(index, view->length);
    return view->data[index];
}

static uint8_t merlo_text_load(const MerloText *text, uint64_t index) {
    if (index >= text->length) merlo_bounds_trap(index, text->length);
    return text->data[index];
}

static MerloText merlo_text_from_bytes(const MerloBytesView *view, uint64_t start, uint64_t end) {
    if (start > end || end > view->length) merlo_bounds_trap(end, view->length);
    MerloText result = { NULL, end - start };
    if (result.length != 0) {
        result.data = (uint8_t *)malloc((size_t)result.length);
        if (result.data == NULL) merlo_allocation_trap();
        memcpy(result.data, view->data + start, (size_t)result.length);
        ++merlo_allocations;
        ++merlo_text_allocations;
        merlo_bytes_copied += result.length;
    }
    return result;
}

static MerloTextBuilder merlo_text_builder_new(void) {
    MerloTextBuilder result = { NULL, 0, 0 };
    return result;
}

static void merlo_text_builder_reserve(MerloTextBuilder *builder, uint64_t additional) {
    if (additional > UINT64_MAX - builder->length) merlo_overflow_trap("TextBuilderLength");
    uint64_t required = builder->length + additional;
    if (required <= builder->capacity) return;
    uint64_t doubled = builder->capacity > UINT64_MAX / 2 ? UINT64_MAX : builder->capacity * 2;
    uint64_t capacity = required > doubled ? required : doubled;
    if (capacity < 8) capacity = 8;
    if (capacity > SIZE_MAX) merlo_overflow_trap("TextBuilderCapacity");
    uint8_t *next = (uint8_t *)realloc(builder->data, (size_t)capacity);
    if (next == NULL) merlo_allocation_trap();
    if (builder->data == NULL) { ++merlo_allocations; }
    builder->data = next;
    builder->capacity = capacity;
}

static void merlo_text_builder_append_byte(MerloTextBuilder *builder, uint64_t byte) {
    if (byte > 255) merlo_bounds_trap(byte, 256);
    merlo_text_builder_reserve(builder, 1);
    builder->data[builder->length++] = (uint8_t)byte;
}

static void merlo_text_builder_append_scalar(MerloTextBuilder *builder, uint64_t scalar) {
    if (scalar > UINT64_C(0x10ffff) || (scalar >= UINT64_C(0xd800) && scalar <= UINT64_C(0xdfff))) {
        merlo_ownership_trap("InvalidUnicodeScalar");
    }
    if (scalar <= UINT64_C(0x7f)) {
        merlo_text_builder_append_byte(builder, scalar);
    } else if (scalar <= UINT64_C(0x7ff)) {
        merlo_text_builder_reserve(builder, 2);
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0xc0) | (scalar >> 6));
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0x80) | (scalar & 63));
    } else if (scalar <= UINT64_C(0xffff)) {
        merlo_text_builder_reserve(builder, 3);
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0xe0) | (scalar >> 12));
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 6) & 63));
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0x80) | (scalar & 63));
    } else {
        merlo_text_builder_reserve(builder, 4);
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0xf0) | (scalar >> 18));
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 12) & 63));
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0x80) | ((scalar >> 6) & 63));
        builder->data[builder->length++] = (uint8_t)(UINT64_C(0x80) | (scalar & 63));
    }
}

static MerloText merlo_text_builder_finish(MerloTextBuilder *builder) {
    MerloText result = { builder->data, builder->length };
    if (builder->data != NULL) ++merlo_text_allocations;
    builder->data = NULL;
    builder->length = 0;
    builder->capacity = 0;
    return result;
}

static MerloBox_UInt64 merlo_zero_Box_UInt64_(void);
static MerloBox_UInt64 merlo_move_Box_UInt64_(MerloBox_UInt64 *value);
static void merlo_drop_Box_UInt64_(MerloBox_UInt64 *value);
static MerloBytes merlo_zero_Bytes(void);
static MerloBytes merlo_move_Bytes(MerloBytes *value);
static void merlo_drop_Bytes(MerloBytes *value);
static Merlo_Json merlo_zero_Json(void);
static Merlo_Json merlo_move_Json(Merlo_Json *value);
static void merlo_drop_Json(Merlo_Json *value);
static Merlo_JsonField merlo_zero_JsonField(void);
static Merlo_JsonField merlo_move_JsonField(Merlo_JsonField *value);
static void merlo_drop_JsonField(Merlo_JsonField *value);
static Merlo_Parser merlo_zero_Parser(void);
static Merlo_Parser merlo_move_Parser(Merlo_Parser *value);
static void merlo_drop_Parser(Merlo_Parser *value);
static MerloText merlo_zero_Text(void);
static MerloText merlo_move_Text(MerloText *value);
static void merlo_drop_Text(MerloText *value);
static MerloTextBuilder merlo_zero_TextBuilder(void);
static MerloTextBuilder merlo_move_TextBuilder(MerloTextBuilder *value);
static void merlo_drop_TextBuilder(MerloTextBuilder *value);
static MerloVec_JsonField merlo_zero_Vec_JsonField_(void);
static MerloVec_JsonField merlo_move_Vec_JsonField_(MerloVec_JsonField *value);
static void merlo_drop_Vec_JsonField_(MerloVec_JsonField *value);
static MerloVec_Json merlo_zero_Vec_Json_(void);
static MerloVec_Json merlo_move_Vec_Json_(MerloVec_Json *value);
static void merlo_drop_Vec_Json_(MerloVec_Json *value);
static MerloVec_Token merlo_zero_Vec_Token_(void);
static MerloVec_Token merlo_move_Vec_Token_(MerloVec_Token *value);
static void merlo_drop_Vec_Token_(MerloVec_Token *value);
static MerloBox_UInt64 merlo_zero_Box_UInt64_(void) {
    MerloBox_UInt64 result;
    memset(&result, 0, sizeof(result));
    return result;
}
static MerloBox_UInt64 merlo_move_Box_UInt64_(MerloBox_UInt64 *value) {
    MerloBox_UInt64 result = *value;
    *value = merlo_zero_Box_UInt64_();
    return result;
}
static void merlo_drop_Box_UInt64_(MerloBox_UInt64 *value) {
    if (value->data == NULL) return;
    free(value->data); value->data = NULL;
    ++merlo_frees; ++merlo_box_frees; ++merlo_drop_calls;
}
static MerloBytes merlo_zero_Bytes(void) {
    MerloBytes result;
    memset(&result, 0, sizeof(result));
    return result;
}
static MerloBytes merlo_move_Bytes(MerloBytes *value) {
    MerloBytes result = *value;
    *value = merlo_zero_Bytes();
    return result;
}
static void merlo_drop_Bytes(MerloBytes *value) {
    if (value->data == NULL) return;
    free(value->data); value->data = NULL; value->length = 0;
    ++merlo_frees; ++merlo_drop_calls;
}
static Merlo_Json merlo_zero_Json(void) {
    Merlo_Json result;
    memset(&result, 0, sizeof(result));
    result.tag = MERLO_Json_MOVED_TAG;
    return result;
}
static Merlo_Json merlo_move_Json(Merlo_Json *value) {
    Merlo_Json result = *value;
    *value = merlo_zero_Json();
    return result;
}
static void merlo_drop_Json(Merlo_Json *value) {
    if (value->tag == MERLO_Json_MOVED_TAG) return;
    switch (value->tag) {
    case UINT32_C(0):
        break;
    case UINT32_C(1):
        break;
    case UINT32_C(2):
        merlo_drop_Text(&value->payload.Number);
        break;
    case UINT32_C(3):
        merlo_drop_Text(&value->payload.String);
        break;
    case UINT32_C(4):
        merlo_drop_Vec_Json_(&value->payload.Array);
        break;
    case UINT32_C(5):
        merlo_drop_Vec_JsonField_(&value->payload.Object);
        break;
    default: merlo_ownership_trap("InvalidEnumTagDuringDrop");
    }
    value->tag = MERLO_Json_MOVED_TAG;
    ++merlo_ast_nodes_freed; ++merlo_drop_calls;
}
static Merlo_JsonField merlo_zero_JsonField(void) {
    Merlo_JsonField result;
    memset(&result, 0, sizeof(result));
    result.key = merlo_zero_Text();
    result.value = merlo_zero_Json();
    return result;
}
static Merlo_JsonField merlo_move_JsonField(Merlo_JsonField *value) {
    Merlo_JsonField result = *value;
    *value = merlo_zero_JsonField();
    return result;
}
static void merlo_drop_JsonField(Merlo_JsonField *value) {
    merlo_drop_Text(&value->key);
    merlo_drop_Json(&value->value);
    ++merlo_drop_calls;
}
static Merlo_Parser merlo_zero_Parser(void) {
    Merlo_Parser result;
    memset(&result, 0, sizeof(result));
    result.tokens = merlo_zero_Vec_Token_();
    return result;
}
static Merlo_Parser merlo_move_Parser(Merlo_Parser *value) {
    Merlo_Parser result = *value;
    *value = merlo_zero_Parser();
    return result;
}
static void merlo_drop_Parser(Merlo_Parser *value) {
    merlo_drop_Vec_Token_(&value->tokens);
    ++merlo_drop_calls;
}
static MerloText merlo_zero_Text(void) {
    MerloText result;
    memset(&result, 0, sizeof(result));
    return result;
}
static MerloText merlo_move_Text(MerloText *value) {
    MerloText result = *value;
    *value = merlo_zero_Text();
    return result;
}
static void merlo_drop_Text(MerloText *value) {
    if (value->data == NULL) return;
    free(value->data);
    value->data = NULL; value->length = 0;
    ++merlo_frees; ++merlo_text_frees; ++merlo_drop_calls;
}
static MerloTextBuilder merlo_zero_TextBuilder(void) {
    MerloTextBuilder result;
    memset(&result, 0, sizeof(result));
    return result;
}
static MerloTextBuilder merlo_move_TextBuilder(MerloTextBuilder *value) {
    MerloTextBuilder result = *value;
    *value = merlo_zero_TextBuilder();
    return result;
}
static void merlo_drop_TextBuilder(MerloTextBuilder *value) {
    if (value->data == NULL) return;
    free(value->data);
    value->data = NULL; value->length = 0; value->capacity = 0;
    ++merlo_frees; ++merlo_drop_calls;
}
static MerloVec_JsonField merlo_zero_Vec_JsonField_(void) {
    MerloVec_JsonField result;
    memset(&result, 0, sizeof(result));
    return result;
}
static MerloVec_JsonField merlo_move_Vec_JsonField_(MerloVec_JsonField *value) {
    MerloVec_JsonField result = *value;
    *value = merlo_zero_Vec_JsonField_();
    return result;
}
static void merlo_drop_Vec_JsonField_(MerloVec_JsonField *value) {
    if (value->active_views != 0) merlo_ownership_trap("VecDropDuringView");
    for (uint64_t index = 0; index < value->length; ++index) {
        merlo_drop_JsonField(&value->data[index]);
        ++merlo_vec_elements_dropped;
    }
    if (value->data != NULL) { free(value->data); ++merlo_frees; ++merlo_vec_frees; }
    value->data = NULL; value->length = 0; value->capacity = 0; value->active_views = 0;
    ++merlo_drop_calls;
}
static MerloVec_Json merlo_zero_Vec_Json_(void) {
    MerloVec_Json result;
    memset(&result, 0, sizeof(result));
    return result;
}
static MerloVec_Json merlo_move_Vec_Json_(MerloVec_Json *value) {
    MerloVec_Json result = *value;
    *value = merlo_zero_Vec_Json_();
    return result;
}
static void merlo_drop_Vec_Json_(MerloVec_Json *value) {
    if (value->active_views != 0) merlo_ownership_trap("VecDropDuringView");
    for (uint64_t index = 0; index < value->length; ++index) {
        merlo_drop_Json(&value->data[index]);
        ++merlo_vec_elements_dropped;
    }
    if (value->data != NULL) { free(value->data); ++merlo_frees; ++merlo_vec_frees; }
    value->data = NULL; value->length = 0; value->capacity = 0; value->active_views = 0;
    ++merlo_drop_calls;
}
static MerloVec_Token merlo_zero_Vec_Token_(void) {
    MerloVec_Token result;
    memset(&result, 0, sizeof(result));
    return result;
}
static MerloVec_Token merlo_move_Vec_Token_(MerloVec_Token *value) {
    MerloVec_Token result = *value;
    *value = merlo_zero_Vec_Token_();
    return result;
}
static void merlo_drop_Vec_Token_(MerloVec_Token *value) {
    if (value->active_views != 0) merlo_ownership_trap("VecDropDuringView");
    merlo_vec_elements_dropped += value->length;
    if (value->data != NULL) { free(value->data); ++merlo_frees; ++merlo_vec_frees; }
    value->data = NULL; value->length = 0; value->capacity = 0; value->active_views = 0;
    ++merlo_drop_calls;
}

static Merlo_Json merlo_make_Json_Null(void) {
    Merlo_Json result;
    result.tag = UINT32_C(0);
    ++merlo_ast_nodes_allocated;
    return result;
}
static Merlo_Json merlo_make_Json_Bool(bool value) {
    Merlo_Json result;
    result.tag = UINT32_C(1);
    result.payload.Bool = value;
    ++merlo_ast_nodes_allocated;
    return result;
}
static Merlo_Json merlo_make_Json_Number(MerloText value) {
    Merlo_Json result;
    result.tag = UINT32_C(2);
    result.payload.Number = value;
    ++merlo_ast_nodes_allocated;
    return result;
}
static Merlo_Json merlo_make_Json_String(MerloText value) {
    Merlo_Json result;
    result.tag = UINT32_C(3);
    result.payload.String = value;
    ++merlo_ast_nodes_allocated;
    return result;
}
static Merlo_Json merlo_make_Json_Array(MerloVec_Json value) {
    Merlo_Json result;
    result.tag = UINT32_C(4);
    result.payload.Array = value;
    ++merlo_ast_nodes_allocated;
    return result;
}
static Merlo_Json merlo_make_Json_Object(MerloVec_JsonField value) {
    Merlo_Json result;
    result.tag = UINT32_C(5);
    result.payload.Object = value;
    ++merlo_ast_nodes_allocated;
    return result;
}
static Merlo_JsonField merlo_make_JsonField(MerloText key, Merlo_Json value) {
    Merlo_JsonField result;
    result.key = key;
    result.value = value;
    return result;
}
static Merlo_Parser merlo_make_Parser(MerloBytesView input, MerloVec_Token tokens, uint64_t index, Merlo_ErrorKind error, uint64_t error_offset) {
    Merlo_Parser result;
    result.input = input;
    result.tokens = tokens;
    result.index = index;
    result.error = error;
    result.error_offset = error_offset;
    return result;
}
static Merlo_ProgramResult merlo_make_ProgramResult(bool ok, Merlo_ErrorKind error, uint64_t error_offset, uint64_t nodes, uint64_t arrays, uint64_t objects, uint64_t fields, uint64_t checksum) {
    Merlo_ProgramResult result;
    result.ok = ok;
    result.error = error;
    result.error_offset = error_offset;
    result.nodes = nodes;
    result.arrays = arrays;
    result.objects = objects;
    result.fields = fields;
    result.checksum = checksum;
    return result;
}
static Merlo_Stats merlo_make_Stats(uint64_t nodes, uint64_t nulls, uint64_t bools, uint64_t numbers, uint64_t strings, uint64_t arrays, uint64_t objects, uint64_t fields, uint64_t checksum) {
    Merlo_Stats result;
    result.nodes = nodes;
    result.nulls = nulls;
    result.bools = bools;
    result.numbers = numbers;
    result.strings = strings;
    result.arrays = arrays;
    result.objects = objects;
    result.fields = fields;
    result.checksum = checksum;
    return result;
}
static Merlo_Token merlo_make_Token(Merlo_TokenKind kind, uint64_t start, uint64_t end, bool escaped) {
    Merlo_Token result;
    result.kind = kind;
    result.start = start;
    result.end = end;
    result.escaped = escaped;
    return result;
}

static MerloBox_UInt64 merlo_Box_UInt64__new(uint64_t value) {
    MerloBox_UInt64 result; result.data = (uint64_t *)malloc(sizeof(uint64_t));
    if (result.data == NULL) merlo_allocation_trap();
    *result.data = value; ++merlo_allocations; ++merlo_box_allocations; return result;
}
static uint64_t *merlo_Box_UInt64__get(MerloBox_UInt64 *value) { if (value->data == NULL) merlo_ownership_trap("BoxUseAfterMove"); return value->data; }
static MerloVec_JsonField merlo_Vec_JsonField__new(void) { MerloVec_JsonField result = { NULL, 0, 0, 0 }; return result; }
static uint64_t merlo_Vec_JsonField__len(const MerloVec_JsonField *value) { return value->length; }
static uint64_t merlo_Vec_JsonField__capacity(const MerloVec_JsonField *value) { return value->capacity; }
static Merlo_JsonField *merlo_Vec_JsonField__get(MerloVec_JsonField *value, uint64_t index) {
    if (index >= value->length) merlo_bounds_trap(index, value->length);
    return &value->data[index];
}
static void merlo_Vec_JsonField__push(MerloVec_JsonField *value, Merlo_JsonField element) {
    if (value->length == UINT64_MAX) merlo_overflow_trap("VecLength");
    uint64_t required = value->length + 1;
    if (required > value->capacity) {
        if (value->active_views != 0) merlo_ownership_trap("VecGrowthDuringView");
        uint64_t doubled = value->capacity > UINT64_MAX / 2 ? UINT64_MAX : value->capacity * 2;
        uint64_t capacity = required > doubled ? required : doubled;
        if (capacity < 4) capacity = 4;
        if (capacity > SIZE_MAX / sizeof(Merlo_JsonField)) merlo_overflow_trap("VecCapacity");
        Merlo_JsonField *next = (Merlo_JsonField *)realloc(value->data, (size_t)capacity * sizeof(Merlo_JsonField));
        if (next == NULL) merlo_allocation_trap();
        if (value->data == NULL) { ++merlo_allocations; ++merlo_vec_allocations; } else { ++merlo_vec_reallocations; }
        value->data = next; value->capacity = capacity; ++merlo_vec_growths;
    }
    value->data[value->length++] = element; ++merlo_vec_initialized;
}
static MerloVec_Json merlo_Vec_Json__new(void) { MerloVec_Json result = { NULL, 0, 0, 0 }; return result; }
static uint64_t merlo_Vec_Json__len(const MerloVec_Json *value) { return value->length; }
static uint64_t merlo_Vec_Json__capacity(const MerloVec_Json *value) { return value->capacity; }
static Merlo_Json *merlo_Vec_Json__get(MerloVec_Json *value, uint64_t index) {
    if (index >= value->length) merlo_bounds_trap(index, value->length);
    return &value->data[index];
}
static void merlo_Vec_Json__push(MerloVec_Json *value, Merlo_Json element) {
    if (value->length == UINT64_MAX) merlo_overflow_trap("VecLength");
    uint64_t required = value->length + 1;
    if (required > value->capacity) {
        if (value->active_views != 0) merlo_ownership_trap("VecGrowthDuringView");
        uint64_t doubled = value->capacity > UINT64_MAX / 2 ? UINT64_MAX : value->capacity * 2;
        uint64_t capacity = required > doubled ? required : doubled;
        if (capacity < 4) capacity = 4;
        if (capacity > SIZE_MAX / sizeof(Merlo_Json)) merlo_overflow_trap("VecCapacity");
        Merlo_Json *next = (Merlo_Json *)realloc(value->data, (size_t)capacity * sizeof(Merlo_Json));
        if (next == NULL) merlo_allocation_trap();
        if (value->data == NULL) { ++merlo_allocations; ++merlo_vec_allocations; } else { ++merlo_vec_reallocations; }
        value->data = next; value->capacity = capacity; ++merlo_vec_growths;
    }
    value->data[value->length++] = element; ++merlo_vec_initialized;
}
static MerloVec_Token merlo_Vec_Token__new(void) { MerloVec_Token result = { NULL, 0, 0, 0 }; return result; }
static uint64_t merlo_Vec_Token__len(const MerloVec_Token *value) { return value->length; }
static uint64_t merlo_Vec_Token__capacity(const MerloVec_Token *value) { return value->capacity; }
static Merlo_Token *merlo_Vec_Token__get(MerloVec_Token *value, uint64_t index) {
    if (index >= value->length) merlo_bounds_trap(index, value->length);
    return &value->data[index];
}
static void merlo_Vec_Token__push(MerloVec_Token *value, Merlo_Token element) {
    if (value->length == UINT64_MAX) merlo_overflow_trap("VecLength");
    uint64_t required = value->length + 1;
    if (required > value->capacity) {
        if (value->active_views != 0) merlo_ownership_trap("VecGrowthDuringView");
        uint64_t doubled = value->capacity > UINT64_MAX / 2 ? UINT64_MAX : value->capacity * 2;
        uint64_t capacity = required > doubled ? required : doubled;
        if (capacity < 4) capacity = 4;
        if (capacity > SIZE_MAX / sizeof(Merlo_Token)) merlo_overflow_trap("VecCapacity");
        Merlo_Token *next = (Merlo_Token *)realloc(value->data, (size_t)capacity * sizeof(Merlo_Token));
        if (next == NULL) merlo_allocation_trap();
        if (value->data == NULL) { ++merlo_allocations; ++merlo_vec_allocations; } else { ++merlo_vec_reallocations; }
        value->data = next; value->capacity = capacity; ++merlo_vec_growths;
    }
    value->data[value->length++] = element; ++merlo_vec_initialized;
}

static void merlo_fn_set_error(Merlo_Parser *parser, Merlo_ErrorKind kind, uint64_t offset) {
    if ((((parser)->error == MERLO_ErrorKind_NoError))) {
        (parser)->error = kind;
        (parser)->error_offset = offset;
    }
    return;
}

static bool merlo_fn_is_space(uint64_t byte) {
    bool __merlo_return_1 = (((byte == UINT64_C(32))) || ((byte == UINT64_C(9))) || ((byte == UINT64_C(10))) || ((byte == UINT64_C(13))));
    return __merlo_return_1;
}

static bool merlo_fn_is_digit(uint64_t byte) {
    bool __merlo_return_2 = (((byte >= UINT64_C(48))) && ((byte <= UINT64_C(57))));
    return __merlo_return_2;
}

static bool merlo_fn_is_digit_one_to_nine(uint64_t byte) {
    bool __merlo_return_3 = (((byte >= UINT64_C(49))) && ((byte <= UINT64_C(57))));
    return __merlo_return_3;
}

static uint64_t merlo_fn_hex_value(uint64_t byte) {
    if ((((byte >= UINT64_C(48))) && ((byte <= UINT64_C(57))))) {
        uint64_t __merlo_return_4 = (byte - UINT64_C(48));
        return __merlo_return_4;
    }
    if ((((byte >= UINT64_C(65))) && ((byte <= UINT64_C(70))))) {
        uint64_t __merlo_return_5 = (byte - UINT64_C(55));
        return __merlo_return_5;
    }
    if ((((byte >= UINT64_C(97))) && ((byte <= UINT64_C(102))))) {
        uint64_t __merlo_return_6 = (byte - UINT64_C(87));
        return __merlo_return_6;
    }
    uint64_t __merlo_return_7 = UINT64_C(256);
    return __merlo_return_7;
}

static uint64_t merlo_fn_box_smoke(uint64_t value) {
    MerloBox_UInt64 item = merlo_zero_Box_UInt64_();
    item = merlo_Box_UInt64__new(value);
    uint64_t __merlo_return_8 = (*merlo_Box_UInt64__get(&(item)));
    merlo_drop_Box_UInt64_(&item);
    return __merlo_return_8;
}

static bool merlo_fn_validate_utf8(Merlo_Parser *parser) {
    uint64_t index = {0};
    uint64_t first = {0};
    uint64_t needed = {0};
    uint64_t scalar = {0};
    uint64_t minimum = {0};
    uint64_t part = {0};
    uint64_t continuation = {0};
    index = UINT64_C(0);
    while (((index < (&((parser)->input))->length))) {
        first = merlo_bytes_load(&((parser)->input), index);
        if (((first < UINT64_C(128)))) {
            index = (index + UINT64_C(1));
        } else {
            needed = UINT64_C(0);
            scalar = UINT64_C(0);
            minimum = UINT64_C(0);
            if ((((first >= UINT64_C(194))) && ((first <= UINT64_C(223))))) {
                needed = UINT64_C(1);
                scalar = (first - UINT64_C(192));
                minimum = UINT64_C(128);
            } else {
                if ((((first >= UINT64_C(224))) && ((first <= UINT64_C(239))))) {
                    needed = UINT64_C(2);
                    scalar = (first - UINT64_C(224));
                    minimum = UINT64_C(2048);
                } else {
                    if ((((first >= UINT64_C(240))) && ((first <= UINT64_C(244))))) {
                        needed = UINT64_C(3);
                        scalar = (first - UINT64_C(240));
                        minimum = UINT64_C(65536);
                    } else {
                        (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidUtf8, index);
                        bool __merlo_return_9 = false;
                        return __merlo_return_9;
                    }
                }
            }
            if ((((index + needed) >= (&((parser)->input))->length))) {
                (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidUtf8, index);
                bool __merlo_return_10 = false;
                return __merlo_return_10;
            }
            part = UINT64_C(0);
            while (((part < needed))) {
                continuation = merlo_bytes_load(&((parser)->input), ((index + part) + UINT64_C(1)));
                if ((((continuation < UINT64_C(128))) || ((continuation > UINT64_C(191))))) {
                    (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidUtf8, ((index + part) + UINT64_C(1)));
                    bool __merlo_return_11 = false;
                    return __merlo_return_11;
                }
                scalar = (((scalar * UINT64_C(64)) + continuation) - UINT64_C(128));
                part = (part + UINT64_C(1));
            }
            if ((((scalar < minimum)) || ((scalar > UINT64_C(1114111))) || (((scalar >= UINT64_C(55296))) && ((scalar <= UINT64_C(57343)))))) {
                (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidUtf8, index);
                bool __merlo_return_12 = false;
                return __merlo_return_12;
            }
            index = ((index + needed) + UINT64_C(1));
        }
    }
    bool __merlo_return_13 = true;
    return __merlo_return_13;
}

static void merlo_fn_push_token(Merlo_Parser *parser, Merlo_TokenKind kind, uint64_t start, uint64_t end, bool escaped) {
    merlo_Vec_Token__push(&((parser)->tokens), merlo_make_Token(kind, start, end, escaped));
    return;
}

static uint64_t merlo_fn_scan_string(Merlo_Parser *parser, uint64_t quote) {
    uint64_t index = {0};
    bool escaped = {0};
    uint64_t byte = {0};
    uint64_t escape = {0};
    uint64_t digit = {0};
    index = (quote + UINT64_C(1));
    escaped = false;
    while (((index < (&((parser)->input))->length))) {
        byte = merlo_bytes_load(&((parser)->input), index);
        if (((byte == UINT64_C(34)))) {
            (void)merlo_fn_push_token(parser, MERLO_TokenKind_String, (quote + UINT64_C(1)), index, escaped);
            uint64_t __merlo_return_14 = (index + UINT64_C(1));
            return __merlo_return_14;
        }
        if (((byte < UINT64_C(32)))) {
            (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidString, index);
            uint64_t __merlo_return_15 = (&((parser)->input))->length;
            return __merlo_return_15;
        }
        if (((byte == UINT64_C(92)))) {
            escaped = true;
            index = (index + UINT64_C(1));
            if (((index >= (&((parser)->input))->length))) {
                (void)merlo_fn_set_error(parser, MERLO_ErrorKind_UnexpectedEnd, index);
                uint64_t __merlo_return_16 = (&((parser)->input))->length;
                return __merlo_return_16;
            }
            escape = merlo_bytes_load(&((parser)->input), index);
            if (((escape == UINT64_C(117)))) {
                if ((((index + UINT64_C(4)) >= (&((parser)->input))->length))) {
                    (void)merlo_fn_set_error(parser, MERLO_ErrorKind_UnexpectedEnd, index);
                    uint64_t __merlo_return_17 = (&((parser)->input))->length;
                    return __merlo_return_17;
                }
                digit = UINT64_C(1);
                while (((digit <= UINT64_C(4)))) {
                    if (((merlo_fn_hex_value(merlo_bytes_load(&((parser)->input), (index + digit))) == UINT64_C(256)))) {
                        (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidEscape, (index + digit));
                        uint64_t __merlo_return_18 = (&((parser)->input))->length;
                        return __merlo_return_18;
                    }
                    digit = (digit + UINT64_C(1));
                }
                index = (index + UINT64_C(4));
            } else {
                if ((!(((escape == UINT64_C(34))) || ((escape == UINT64_C(92))) || ((escape == UINT64_C(47))) || ((escape == UINT64_C(98))) || ((escape == UINT64_C(102))) || ((escape == UINT64_C(110))) || ((escape == UINT64_C(114))) || ((escape == UINT64_C(116)))))) {
                    (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidEscape, index);
                    uint64_t __merlo_return_19 = (&((parser)->input))->length;
                    return __merlo_return_19;
                }
            }
        }
        index = (index + UINT64_C(1));
    }
    (void)merlo_fn_set_error(parser, MERLO_ErrorKind_UnexpectedEnd, index);
    uint64_t __merlo_return_20 = index;
    return __merlo_return_20;
}

static uint64_t merlo_fn_scan_number(Merlo_Parser *parser, uint64_t start) {
    uint64_t index = {0};
    index = start;
    if (((merlo_bytes_load(&((parser)->input), index) == UINT64_C(45)))) {
        index = (index + UINT64_C(1));
        if (((index >= (&((parser)->input))->length))) {
            (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidNumber, index);
            uint64_t __merlo_return_21 = index;
            return __merlo_return_21;
        }
    }
    if (((merlo_bytes_load(&((parser)->input), index) == UINT64_C(48)))) {
        index = (index + UINT64_C(1));
        if ((((index < (&((parser)->input))->length)) && merlo_fn_is_digit(merlo_bytes_load(&((parser)->input), index)))) {
            (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidNumber, index);
            uint64_t __merlo_return_22 = index;
            return __merlo_return_22;
        }
    } else {
        if ((!merlo_fn_is_digit_one_to_nine(merlo_bytes_load(&((parser)->input), index)))) {
            (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidNumber, index);
            uint64_t __merlo_return_23 = index;
            return __merlo_return_23;
        }
        while ((((index < (&((parser)->input))->length)) && merlo_fn_is_digit(merlo_bytes_load(&((parser)->input), index)))) {
            index = (index + UINT64_C(1));
        }
    }
    if ((((index < (&((parser)->input))->length)) && ((merlo_bytes_load(&((parser)->input), index) == UINT64_C(46))))) {
        index = (index + UINT64_C(1));
        if ((((index >= (&((parser)->input))->length)) || (!merlo_fn_is_digit(merlo_bytes_load(&((parser)->input), index))))) {
            (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidNumber, index);
            uint64_t __merlo_return_24 = index;
            return __merlo_return_24;
        }
        while ((((index < (&((parser)->input))->length)) && merlo_fn_is_digit(merlo_bytes_load(&((parser)->input), index)))) {
            index = (index + UINT64_C(1));
        }
    }
    if ((((index < (&((parser)->input))->length)) && (((merlo_bytes_load(&((parser)->input), index) == UINT64_C(101))) || ((merlo_bytes_load(&((parser)->input), index) == UINT64_C(69)))))) {
        index = (index + UINT64_C(1));
        if ((((index < (&((parser)->input))->length)) && (((merlo_bytes_load(&((parser)->input), index) == UINT64_C(43))) || ((merlo_bytes_load(&((parser)->input), index) == UINT64_C(45)))))) {
            index = (index + UINT64_C(1));
        }
        if ((((index >= (&((parser)->input))->length)) || (!merlo_fn_is_digit(merlo_bytes_load(&((parser)->input), index))))) {
            (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidNumber, index);
            uint64_t __merlo_return_25 = index;
            return __merlo_return_25;
        }
        while ((((index < (&((parser)->input))->length)) && merlo_fn_is_digit(merlo_bytes_load(&((parser)->input), index)))) {
            index = (index + UINT64_C(1));
        }
    }
    (void)merlo_fn_push_token(parser, MERLO_TokenKind_Number, start, index, false);
    uint64_t __merlo_return_26 = index;
    return __merlo_return_26;
}

static uint64_t merlo_fn_match_literal(Merlo_Parser *parser, uint64_t start, uint64_t a, uint64_t b, uint64_t c, uint64_t d, uint64_t e, uint64_t length, Merlo_TokenKind kind) {
    if ((((start + length) > (&((parser)->input))->length))) {
        (void)merlo_fn_set_error(parser, MERLO_ErrorKind_UnexpectedEnd, start);
        uint64_t __merlo_return_27 = (&((parser)->input))->length;
        return __merlo_return_27;
    }
    if ((((merlo_bytes_load(&((parser)->input), start) != a)) || ((merlo_bytes_load(&((parser)->input), (start + UINT64_C(1))) != b)) || ((merlo_bytes_load(&((parser)->input), (start + UINT64_C(2))) != c)))) {
        (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidLiteral, start);
        uint64_t __merlo_return_28 = (&((parser)->input))->length;
        return __merlo_return_28;
    }
    if ((((length == UINT64_C(4))) && ((merlo_bytes_load(&((parser)->input), (start + UINT64_C(3))) != d)))) {
        (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidLiteral, start);
        uint64_t __merlo_return_29 = (&((parser)->input))->length;
        return __merlo_return_29;
    }
    if ((((length == UINT64_C(5))) && ((merlo_bytes_load(&((parser)->input), (start + UINT64_C(4))) != e)))) {
        (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidLiteral, start);
        uint64_t __merlo_return_30 = (&((parser)->input))->length;
        return __merlo_return_30;
    }
    (void)merlo_fn_push_token(parser, kind, start, (start + length), false);
    uint64_t __merlo_return_31 = (start + length);
    return __merlo_return_31;
}

static void merlo_fn_tokenize(Merlo_Parser *parser) {
    uint64_t index = {0};
    uint64_t byte = {0};
    index = UINT64_C(0);
    while ((((index < (&((parser)->input))->length)) && (((parser)->error == MERLO_ErrorKind_NoError)))) {
        byte = merlo_bytes_load(&((parser)->input), index);
        if (merlo_fn_is_space(byte)) {
            index = (index + UINT64_C(1));
        } else {
            if (((byte == UINT64_C(123)))) {
                (void)merlo_fn_push_token(parser, MERLO_TokenKind_LBrace, index, (index + UINT64_C(1)), false);
                index = (index + UINT64_C(1));
            } else {
                if (((byte == UINT64_C(125)))) {
                    (void)merlo_fn_push_token(parser, MERLO_TokenKind_RBrace, index, (index + UINT64_C(1)), false);
                    index = (index + UINT64_C(1));
                } else {
                    if (((byte == UINT64_C(91)))) {
                        (void)merlo_fn_push_token(parser, MERLO_TokenKind_LBracket, index, (index + UINT64_C(1)), false);
                        index = (index + UINT64_C(1));
                    } else {
                        if (((byte == UINT64_C(93)))) {
                            (void)merlo_fn_push_token(parser, MERLO_TokenKind_RBracket, index, (index + UINT64_C(1)), false);
                            index = (index + UINT64_C(1));
                        } else {
                            if (((byte == UINT64_C(58)))) {
                                (void)merlo_fn_push_token(parser, MERLO_TokenKind_Colon, index, (index + UINT64_C(1)), false);
                                index = (index + UINT64_C(1));
                            } else {
                                if (((byte == UINT64_C(44)))) {
                                    (void)merlo_fn_push_token(parser, MERLO_TokenKind_Comma, index, (index + UINT64_C(1)), false);
                                    index = (index + UINT64_C(1));
                                } else {
                                    if (((byte == UINT64_C(34)))) {
                                        index = merlo_fn_scan_string(parser, index);
                                    } else {
                                        if (((byte == UINT64_C(110)))) {
                                            index = merlo_fn_match_literal(parser, index, UINT64_C(110), UINT64_C(117), UINT64_C(108), UINT64_C(108), UINT64_C(0), UINT64_C(4), MERLO_TokenKind_Null);
                                        } else {
                                            if (((byte == UINT64_C(116)))) {
                                                index = merlo_fn_match_literal(parser, index, UINT64_C(116), UINT64_C(114), UINT64_C(117), UINT64_C(101), UINT64_C(0), UINT64_C(4), MERLO_TokenKind_TrueLiteral);
                                            } else {
                                                if (((byte == UINT64_C(102)))) {
                                                    index = merlo_fn_match_literal(parser, index, UINT64_C(102), UINT64_C(97), UINT64_C(108), UINT64_C(115), UINT64_C(101), UINT64_C(5), MERLO_TokenKind_FalseLiteral);
                                                } else {
                                                    if ((((byte == UINT64_C(45))) || merlo_fn_is_digit(byte))) {
                                                        index = merlo_fn_scan_number(parser, index);
                                                    } else {
                                                        (void)merlo_fn_set_error(parser, MERLO_ErrorKind_UnexpectedByte, index);
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    (void)merlo_fn_push_token(parser, MERLO_TokenKind_End, (&((parser)->input))->length, (&((parser)->input))->length, false);
    return;
}

static Merlo_Token merlo_fn_token_at(Merlo_Parser *parser) {
    if ((((parser)->index >= merlo_Vec_Token__len(&((parser)->tokens))))) {
        Merlo_Token __merlo_return_32 = merlo_make_Token(MERLO_TokenKind_End, (&((parser)->input))->length, (&((parser)->input))->length, false);
        return __merlo_return_32;
    }
    Merlo_Token __merlo_return_33 = (*merlo_Vec_Token__get(&((parser)->tokens), (parser)->index));
    return __merlo_return_33;
}

static Merlo_Token merlo_fn_advance(Merlo_Parser *parser) {
    Merlo_Token token = {0};
    token = merlo_fn_token_at(parser);
    if ((((parser)->index < merlo_Vec_Token__len(&((parser)->tokens))))) {
        (parser)->index = ((parser)->index + UINT64_C(1));
    }
    Merlo_Token __merlo_return_34 = token;
    return __merlo_return_34;
}

static MerloText merlo_fn_decode_string(Merlo_Parser *parser, Merlo_Token *token) {
    MerloTextBuilder builder = merlo_zero_TextBuilder();
    uint64_t index = {0};
    uint64_t byte = {0};
    uint64_t escape = {0};
    uint64_t scalar = {0};
    uint64_t digit = {0};
    uint64_t low = {0};
    if ((!(token)->escaped)) {
        MerloText __merlo_return_35 = merlo_text_from_bytes(&((parser)->input), (token)->start, (token)->end);
        merlo_drop_TextBuilder(&builder);
        return __merlo_return_35;
    }
    builder = merlo_text_builder_new();
    index = (token)->start;
    while (((index < (token)->end))) {
        byte = merlo_bytes_load(&((parser)->input), index);
        if (((byte != UINT64_C(92)))) {
            merlo_text_builder_append_byte(&(builder), byte);
            index = (index + UINT64_C(1));
        } else {
            index = (index + UINT64_C(1));
            escape = merlo_bytes_load(&((parser)->input), index);
            if ((((escape == UINT64_C(34))) || ((escape == UINT64_C(92))) || ((escape == UINT64_C(47))))) {
                merlo_text_builder_append_byte(&(builder), escape);
            } else {
                if (((escape == UINT64_C(98)))) {
                    merlo_text_builder_append_byte(&(builder), UINT64_C(8));
                } else {
                    if (((escape == UINT64_C(102)))) {
                        merlo_text_builder_append_byte(&(builder), UINT64_C(12));
                    } else {
                        if (((escape == UINT64_C(110)))) {
                            merlo_text_builder_append_byte(&(builder), UINT64_C(10));
                        } else {
                            if (((escape == UINT64_C(114)))) {
                                merlo_text_builder_append_byte(&(builder), UINT64_C(13));
                            } else {
                                if (((escape == UINT64_C(116)))) {
                                    merlo_text_builder_append_byte(&(builder), UINT64_C(9));
                                } else {
                                    scalar = UINT64_C(0);
                                    digit = UINT64_C(1);
                                    while (((digit <= UINT64_C(4)))) {
                                        scalar = ((scalar * UINT64_C(16)) + merlo_fn_hex_value(merlo_bytes_load(&((parser)->input), (index + digit))));
                                        digit = (digit + UINT64_C(1));
                                    }
                                    index = (index + UINT64_C(4));
                                    if ((((scalar >= UINT64_C(55296))) && ((scalar <= UINT64_C(56319))))) {
                                        if (((((index + UINT64_C(6)) >= (token)->end)) || ((merlo_bytes_load(&((parser)->input), (index + UINT64_C(1))) != UINT64_C(92))) || ((merlo_bytes_load(&((parser)->input), (index + UINT64_C(2))) != UINT64_C(117))))) {
                                            (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidEscape, index);
                                        } else {
                                            low = UINT64_C(0);
                                            digit = UINT64_C(3);
                                            while (((digit <= UINT64_C(6)))) {
                                                low = ((low * UINT64_C(16)) + merlo_fn_hex_value(merlo_bytes_load(&((parser)->input), (index + digit))));
                                                digit = (digit + UINT64_C(1));
                                            }
                                            if ((((low < UINT64_C(56320))) || ((low > UINT64_C(57343))))) {
                                                (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidEscape, (index + UINT64_C(2)));
                                            } else {
                                                scalar = (((UINT64_C(65536) + ((scalar - UINT64_C(55296)) * UINT64_C(1024))) + low) - UINT64_C(56320));
                                                index = (index + UINT64_C(6));
                                            }
                                        }
                                    } else {
                                        if ((((scalar >= UINT64_C(56320))) && ((scalar <= UINT64_C(57343))))) {
                                            (void)merlo_fn_set_error(parser, MERLO_ErrorKind_InvalidEscape, index);
                                        }
                                    }
                                    if ((((parser)->error == MERLO_ErrorKind_NoError))) {
                                        merlo_text_builder_append_scalar(&(builder), scalar);
                                    }
                                }
                            }
                        }
                    }
                }
            }
            index = (index + UINT64_C(1));
        }
    }
    MerloText __merlo_return_36 = merlo_text_builder_finish(&(builder));
    merlo_drop_TextBuilder(&builder);
    return __merlo_return_36;
}

static Merlo_Json merlo_fn_parse_array(Merlo_Parser *parser, uint64_t depth) {
    MerloVec_Json items = merlo_zero_Vec_Json_();
    Merlo_Json child = merlo_zero_Json();
    Merlo_Token separator = {0};
    items = merlo_Vec_Json__new();
    (void)merlo_fn_advance(parser);
    if ((((merlo_fn_token_at(parser)).kind == MERLO_TokenKind_RBracket))) {
        (void)merlo_fn_advance(parser);
        Merlo_Json __merlo_return_37 = merlo_make_Json_Array(merlo_move_Vec_Json_(&items));
        merlo_drop_Json(&child);
        merlo_drop_Vec_Json_(&items);
        return __merlo_return_37;
    }
    while ((((parser)->error == MERLO_ErrorKind_NoError))) {
        child = merlo_fn_parse_value(parser, (depth + UINT64_C(1)));
        merlo_Vec_Json__push(&(items), merlo_move_Json(&child));
        if ((((parser)->error != MERLO_ErrorKind_NoError))) {
            Merlo_Json __merlo_return_38 = merlo_make_Json_Array(merlo_move_Vec_Json_(&items));
            merlo_drop_Json(&child);
            merlo_drop_Vec_Json_(&items);
            return __merlo_return_38;
        }
        separator = merlo_fn_token_at(parser);
        if ((((separator).kind == MERLO_TokenKind_RBracket))) {
            (void)merlo_fn_advance(parser);
            Merlo_Json __merlo_return_39 = merlo_make_Json_Array(merlo_move_Vec_Json_(&items));
            merlo_drop_Json(&child);
            merlo_drop_Vec_Json_(&items);
            return __merlo_return_39;
        }
        if ((((separator).kind != MERLO_TokenKind_Comma))) {
            (void)merlo_fn_set_error(parser, MERLO_ErrorKind_ExpectedCommaOrEnd, (separator).start);
            Merlo_Json __merlo_return_40 = merlo_make_Json_Array(merlo_move_Vec_Json_(&items));
            merlo_drop_Json(&child);
            merlo_drop_Vec_Json_(&items);
            return __merlo_return_40;
        }
        (void)merlo_fn_advance(parser);
    }
    Merlo_Json __merlo_return_41 = merlo_make_Json_Array(merlo_move_Vec_Json_(&items));
    merlo_drop_Json(&child);
    merlo_drop_Vec_Json_(&items);
    return __merlo_return_41;
}

static Merlo_Json merlo_fn_parse_object(Merlo_Parser *parser, uint64_t depth) {
    MerloVec_JsonField fields = merlo_zero_Vec_JsonField_();
    Merlo_Token key_token = {0};
    MerloText key = merlo_zero_Text();
    Merlo_Token colon = {0};
    Merlo_Json value = merlo_zero_Json();
    Merlo_Token separator = {0};
    fields = merlo_Vec_JsonField__new();
    (void)merlo_fn_advance(parser);
    if ((((merlo_fn_token_at(parser)).kind == MERLO_TokenKind_RBrace))) {
        (void)merlo_fn_advance(parser);
        Merlo_Json __merlo_return_42 = merlo_make_Json_Object(merlo_move_Vec_JsonField_(&fields));
        merlo_drop_Json(&value);
        merlo_drop_Text(&key);
        merlo_drop_Vec_JsonField_(&fields);
        return __merlo_return_42;
    }
    while ((((parser)->error == MERLO_ErrorKind_NoError))) {
        key_token = merlo_fn_token_at(parser);
        if ((((key_token).kind != MERLO_TokenKind_String))) {
            (void)merlo_fn_set_error(parser, MERLO_ErrorKind_UnexpectedToken, (key_token).start);
            Merlo_Json __merlo_return_43 = merlo_make_Json_Object(merlo_move_Vec_JsonField_(&fields));
            merlo_drop_Json(&value);
            merlo_drop_Text(&key);
            merlo_drop_Vec_JsonField_(&fields);
            return __merlo_return_43;
        }
        (void)merlo_fn_advance(parser);
        key = merlo_fn_decode_string(parser, &key_token);
        colon = merlo_fn_token_at(parser);
        if ((((colon).kind != MERLO_TokenKind_Colon))) {
            (void)merlo_fn_set_error(parser, MERLO_ErrorKind_ExpectedColon, (colon).start);
            merlo_Vec_JsonField__push(&(fields), merlo_make_JsonField(merlo_move_Text(&key), merlo_make_Json_Null()));
            Merlo_Json __merlo_return_44 = merlo_make_Json_Object(merlo_move_Vec_JsonField_(&fields));
            merlo_drop_Json(&value);
            merlo_drop_Text(&key);
            merlo_drop_Vec_JsonField_(&fields);
            return __merlo_return_44;
        }
        (void)merlo_fn_advance(parser);
        value = merlo_fn_parse_value(parser, (depth + UINT64_C(1)));
        merlo_Vec_JsonField__push(&(fields), merlo_make_JsonField(merlo_move_Text(&key), merlo_move_Json(&value)));
        if ((((parser)->error != MERLO_ErrorKind_NoError))) {
            Merlo_Json __merlo_return_45 = merlo_make_Json_Object(merlo_move_Vec_JsonField_(&fields));
            merlo_drop_Json(&value);
            merlo_drop_Text(&key);
            merlo_drop_Vec_JsonField_(&fields);
            return __merlo_return_45;
        }
        separator = merlo_fn_token_at(parser);
        if ((((separator).kind == MERLO_TokenKind_RBrace))) {
            (void)merlo_fn_advance(parser);
            Merlo_Json __merlo_return_46 = merlo_make_Json_Object(merlo_move_Vec_JsonField_(&fields));
            merlo_drop_Json(&value);
            merlo_drop_Text(&key);
            merlo_drop_Vec_JsonField_(&fields);
            return __merlo_return_46;
        }
        if ((((separator).kind != MERLO_TokenKind_Comma))) {
            (void)merlo_fn_set_error(parser, MERLO_ErrorKind_ExpectedCommaOrEnd, (separator).start);
            Merlo_Json __merlo_return_47 = merlo_make_Json_Object(merlo_move_Vec_JsonField_(&fields));
            merlo_drop_Json(&value);
            merlo_drop_Text(&key);
            merlo_drop_Vec_JsonField_(&fields);
            return __merlo_return_47;
        }
        (void)merlo_fn_advance(parser);
    }
    Merlo_Json __merlo_return_48 = merlo_make_Json_Object(merlo_move_Vec_JsonField_(&fields));
    merlo_drop_Json(&value);
    merlo_drop_Text(&key);
    merlo_drop_Vec_JsonField_(&fields);
    return __merlo_return_48;
}

static Merlo_Json merlo_fn_parse_value(Merlo_Parser *parser, uint64_t depth) {
    Merlo_Token token = {0};
    token = merlo_fn_token_at(parser);
    if (((depth > UINT64_C(128)))) {
        (void)merlo_fn_set_error(parser, MERLO_ErrorKind_DepthExceeded, (token).start);
        Merlo_Json __merlo_return_49 = merlo_make_Json_Null();
        return __merlo_return_49;
    }
    switch ((token).kind) {
    case UINT32_C(6):
        (void)merlo_fn_advance(parser);
        Merlo_Json __merlo_return_50 = merlo_make_Json_Null();
        return __merlo_return_50;
    case UINT32_C(7):
        (void)merlo_fn_advance(parser);
        Merlo_Json __merlo_return_51 = merlo_make_Json_Bool(true);
        return __merlo_return_51;
    case UINT32_C(8):
        (void)merlo_fn_advance(parser);
        Merlo_Json __merlo_return_52 = merlo_make_Json_Bool(false);
        return __merlo_return_52;
    case UINT32_C(9):
        (void)merlo_fn_advance(parser);
        Merlo_Json __merlo_return_53 = merlo_make_Json_Number(merlo_text_from_bytes(&((parser)->input), (token).start, (token).end));
        return __merlo_return_53;
    case UINT32_C(10):
        (void)merlo_fn_advance(parser);
        Merlo_Json __merlo_return_54 = merlo_make_Json_String(merlo_fn_decode_string(parser, &token));
        return __merlo_return_54;
    case UINT32_C(2):
        Merlo_Json __merlo_return_55 = merlo_fn_parse_array(parser, depth);
        return __merlo_return_55;
    case UINT32_C(0):
        Merlo_Json __merlo_return_56 = merlo_fn_parse_object(parser, depth);
        return __merlo_return_56;
    case UINT32_C(11):
        (void)merlo_fn_set_error(parser, MERLO_ErrorKind_UnexpectedEnd, (token).start);
        Merlo_Json __merlo_return_57 = merlo_make_Json_Null();
        return __merlo_return_57;
    default:
        (void)merlo_fn_set_error(parser, MERLO_ErrorKind_UnexpectedToken, (token).start);
        Merlo_Json __merlo_return_58 = merlo_make_Json_Null();
        return __merlo_return_58;
    }
}

static void merlo_fn_checksum_byte(Merlo_Stats *stats, uint64_t byte) {
    (stats)->checksum = ((((stats)->checksum ^ byte) * UINT64_C(1099511628211)) & UINT64_C(18446744073709551615));
    return;
}

static void merlo_fn_checksum_text(Merlo_Stats *stats, MerloText *text) {
    uint64_t index = {0};
    index = UINT64_C(0);
    while (((index < (text)->length))) {
        (void)merlo_fn_checksum_byte(stats, merlo_text_load(text, index));
        index = (index + UINT64_C(1));
    }
    return;
}

static void merlo_fn_visit_json(Merlo_Json *value, Merlo_Stats *stats) {
    uint64_t index = {0};
    Merlo_JsonField *field = NULL;
    (stats)->nodes = ((stats)->nodes + UINT64_C(1));
    (void)merlo_fn_checksum_byte(stats, (value)->tag);
    switch ((value)->tag) {
    case UINT32_C(0):
        (stats)->nulls = ((stats)->nulls + UINT64_C(1));
        break;
    case UINT32_C(1):
        bool flag = (value)->payload.Bool;
        (stats)->bools = ((stats)->bools + UINT64_C(1));
        if (flag) {
            (void)merlo_fn_checksum_byte(stats, UINT64_C(1));
        } else {
            (void)merlo_fn_checksum_byte(stats, UINT64_C(0));
        }
        break;
    case UINT32_C(2):
        MerloText *raw = &(value)->payload.Number;
        (stats)->numbers = ((stats)->numbers + UINT64_C(1));
        (void)merlo_fn_checksum_text(stats, raw);
        break;
    case UINT32_C(3):
        MerloText *text = &(value)->payload.String;
        (stats)->strings = ((stats)->strings + UINT64_C(1));
        (void)merlo_fn_checksum_text(stats, text);
        break;
    case UINT32_C(4):
        MerloVec_Json *items = &(value)->payload.Array;
        (stats)->arrays = ((stats)->arrays + UINT64_C(1));
        index = UINT64_C(0);
        while (((index < merlo_Vec_Json__len(items)))) {
            (void)merlo_fn_visit_json(merlo_Vec_Json__get(items, index), stats);
            index = (index + UINT64_C(1));
        }
        break;
    case UINT32_C(5):
        MerloVec_JsonField *fields = &(value)->payload.Object;
        (stats)->objects = ((stats)->objects + UINT64_C(1));
        (stats)->fields = ((stats)->fields + merlo_Vec_JsonField__len(fields));
        index = UINT64_C(0);
        while (((index < merlo_Vec_JsonField__len(fields)))) {
            field = merlo_Vec_JsonField__get(fields, index);
            (void)merlo_fn_checksum_text(stats, &((field)->key));
            (void)merlo_fn_visit_json(&((field)->value), stats);
            index = (index + UINT64_C(1));
        }
        break;
    }
    return;
}

static Merlo_ProgramResult merlo_fn_main(MerloBytesView data) {
    Merlo_Parser parser = merlo_zero_Parser();
    Merlo_Json root = merlo_zero_Json();
    Merlo_Stats stats = {0};
    parser = merlo_make_Parser(data, merlo_Vec_Token__new(), UINT64_C(0), MERLO_ErrorKind_NoError, UINT64_C(0));
    if (merlo_fn_validate_utf8(&parser)) {
        (void)merlo_fn_tokenize(&parser);
    }
    root = merlo_fn_parse_value(&parser, UINT64_C(0));
    if (((((parser).error == MERLO_ErrorKind_NoError)) && (((merlo_fn_token_at(&parser)).kind != MERLO_TokenKind_End)))) {
        (void)merlo_fn_set_error(&parser, MERLO_ErrorKind_TrailingTokens, (merlo_fn_token_at(&parser)).start);
    }
    stats = merlo_make_Stats(UINT64_C(0), UINT64_C(0), UINT64_C(0), UINT64_C(0), UINT64_C(0), UINT64_C(0), UINT64_C(0), UINT64_C(0), UINT64_C(1469598103934665603));
    if ((((parser).error == MERLO_ErrorKind_NoError))) {
        (void)merlo_fn_visit_json(&root, &stats);
    }
    Merlo_ProgramResult __merlo_return_59 = merlo_make_ProgramResult((((parser).error == MERLO_ErrorKind_NoError)), (parser).error, (parser).error_offset, (stats).nodes, (stats).arrays, (stats).objects, (stats).fields, (stats).checksum);
    merlo_drop_Json(&root);
    merlo_drop_Parser(&parser);
    return __merlo_return_59;
}

static uint8_t *merlo_host_read_stdin(uint64_t *length) {
    uint8_t *data = NULL;
    size_t used = 0;
    size_t capacity = 0;
    uint8_t chunk[4096];
    while (!feof(stdin)) {
        size_t count = fread(chunk, 1, sizeof(chunk), stdin);
        if (ferror(stdin)) { free(data); return NULL; }
        if (count == 0) break;
        if (used > SIZE_MAX - count) { free(data); return NULL; }
        size_t required = used + count;
        if (required > capacity) {
            size_t next = capacity == 0 ? 4096 : capacity;
            while (next < required) {
                if (next > SIZE_MAX / 2) { next = required; break; }
                next *= 2;
            }
            uint8_t *grown = (uint8_t *)realloc(data, next);
            if (grown == NULL) { free(data); return NULL; }
            data = grown;
            capacity = next;
        }
        memcpy(data + used, chunk, count);
        used += count;
    }
    *length = (uint64_t)used;
    return data;
}

int main(int argc, char **argv) {
    uint64_t repeat = 1;
    if (argc > 1) {
        errno = 0;
        char *end = NULL;
        unsigned long long parsed = strtoull(argv[1], &end, 10);
        if (errno != 0 || end == argv[1] || *end != '\0' || parsed == 0) return 64;
        repeat = (uint64_t)parsed;
    }
    uint64_t length = 0;
    uint8_t *input = merlo_host_read_stdin(&length);
    if (input == NULL && length != 0) return 74;
    MerloBytesView view = { input, length };
    if (merlo_fn_box_smoke(UINT64_C(41)) != UINT64_C(41)) return 70;
    Merlo_ProgramResult result = {0};
    for (uint64_t iteration = 0; iteration < repeat; ++iteration) {
        result = merlo_fn_main(view);
    }
    if (result.ok) {
        printf("OK checksum=%" PRIu64 " nodes=%" PRIu64 " arrays=%" PRIu64 " objects=%" PRIu64 " fields=%" PRIu64 "\n",
               result.checksum, result.nodes, result.arrays, result.objects, result.fields);
    } else {
        printf("ERROR kind=%" PRIu32 " offset=%" PRIu64 "\n", result.error, result.error_offset);
    }
    printf("MERLO_METRICS allocations=%" PRIu64 " frees=%" PRIu64 " text_allocations=%" PRIu64 " text_frees=%" PRIu64
           " vec_allocations=%" PRIu64 " vec_frees=%" PRIu64 " vec_reallocations=%" PRIu64 " vec_growths=%" PRIu64
           " vec_initialized=%" PRIu64 " vec_elements_dropped=%" PRIu64 " box_allocations=%" PRIu64 " box_frees=%" PRIu64
           " ast_nodes_allocated=%" PRIu64 " ast_nodes_freed=%" PRIu64 " bytes_copied=%" PRIu64 " drops=%" PRIu64 "\n",
           merlo_allocations, merlo_frees, merlo_text_allocations, merlo_text_frees,
           merlo_vec_allocations, merlo_vec_frees, merlo_vec_reallocations, merlo_vec_growths,
           merlo_vec_initialized, merlo_vec_elements_dropped, merlo_box_allocations, merlo_box_frees,
           merlo_ast_nodes_allocated, merlo_ast_nodes_freed, merlo_bytes_copied, merlo_drop_calls);
    free(input);
    return result.ok ? 0 : 2;
}
