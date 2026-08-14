/* Readable C reference for the frozen CSV aggregation algorithm. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define U64_MAX_VALUE UINT64_MAX

typedef enum {
    CSV_OK = 0,
    CSV_INVALID_UTF8,
    CSV_READ_ERROR,
    CSV_INVALID_DELIMITER,
    CSV_MISSING_HEADER,
    CSV_INVALID_HEADER,
    CSV_INVALID_ROW,
    CSV_QUANTITY_OVERFLOW,
    CSV_REVENUE_OVERFLOW,
    CSV_COUNT_OVERFLOW,
} CsvError;

typedef struct { char *name; uint64_t value; } CounterEntry;
typedef struct { CounterEntry *entries; size_t length; size_t capacity; } Counter;
typedef struct {
    uint64_t total, valid, invalid, quantity, revenue;
    Counter products, regions;
} Totals;

static void counter_drop(Counter *counter) {
    for (size_t i = 0; i < counter->length; ++i) free(counter->entries[i].name);
    free(counter->entries);
    *counter = (Counter){0};
}

static CsvError counter_increment(Counter *counter, const char *name, uint64_t amount) {
    for (size_t i = 0; i < counter->length; ++i) {
        if (strcmp(counter->entries[i].name, name) == 0) {
            if (counter->entries[i].value > U64_MAX_VALUE - amount) return CSV_COUNT_OVERFLOW;
            counter->entries[i].value += amount;
            return CSV_OK;
        }
    }
    if (counter->length == counter->capacity) {
        size_t next = counter->capacity == 0 ? 8 : counter->capacity * 2;
        CounterEntry *grown = realloc(counter->entries, next * sizeof(*grown));
        if (grown == NULL) return CSV_READ_ERROR;
        counter->entries = grown;
        counter->capacity = next;
    }
    counter->entries[counter->length].name = strdup(name);
    if (counter->entries[counter->length].name == NULL) return CSV_READ_ERROR;
    counter->entries[counter->length].value = amount;
    counter->length++;
    return CSV_OK;
}

static bool valid_utf8(const unsigned char *text, size_t length) {
    for (size_t i = 0; i < length;) {
        unsigned char first = text[i]; size_t needed = 0; uint32_t scalar = 0; uint32_t minimum = 0;
        if (first <= 0x7f) { i++; continue; }
        if (first >= 0xc2 && first <= 0xdf) { needed = 1; scalar = first & 0x1f; minimum = 0x80; }
        else if (first >= 0xe0 && first <= 0xef) { needed = 2; scalar = first & 0x0f; minimum = 0x800; }
        else if (first >= 0xf0 && first <= 0xf4) { needed = 3; scalar = first & 7; minimum = 0x10000; }
        else return false;
        if (needed > length - i - 1) return false;
        for (size_t j = 1; j <= needed; ++j) {
            unsigned char continuation = text[i + j];
            if (continuation < 0x80 || continuation > 0xbf) return false;
            scalar = (scalar << 6) | (continuation & 0x3f);
        }
        if (scalar < minimum || scalar > 0x10ffff || (scalar >= 0xd800 && scalar <= 0xdfff)) return false;
        i += needed + 1;
    }
    return true;
}

static bool checked_add(uint64_t left, uint64_t right, uint64_t *out) {
    if (left > U64_MAX_VALUE - right) return false;
    *out = left + right;
    return true;
}

static bool checked_multiply(uint64_t left, uint64_t right, uint64_t *out) {
    if (right != 0 && left > U64_MAX_VALUE / right) return false;
    *out = left * right;
    return true;
}

static bool parse_uint64(const char *text, uint64_t *out) {
    if (*text == '\0') return false;
    uint64_t value = 0;
    for (const unsigned char *p = (const unsigned char *)text; *p != '\0'; ++p) {
        if (*p < '0' || *p > '9') return false;
        unsigned digit = *p - '0';
        if (value > (U64_MAX_VALUE - digit) / 10) return false;
        value = value * 10 + digit;
    }
    *out = value;
    return true;
}

static void fields_drop(char **fields, size_t length) {
    for (size_t i = 0; i < length; ++i) free(fields[i]);
    free(fields);
}

/* Parse one physical line with RFC-4180 quotes; records cannot span lines here. */
static bool parse_csv_fields(const char *line, char delimiter, char ***out, size_t *count) {
    char **fields = NULL; size_t length = 0; size_t capacity = 0; const char *p = line;
    while (true) {
        size_t value_capacity = 32, value_length = 0;
        char *value = malloc(value_capacity);
        if (value == NULL) { fields_drop(fields, length); return false; }
        bool quoted = *p == '"';
        if (quoted) p++;
        while (*p != '\0') {
            if (quoted) {
                if (*p == '"') {
                    if (p[1] == '"') { p += 2; }
                    else { quoted = false; p++; break; }
                } else value[value_length++] = *p++;
            } else {
                if (*p == delimiter) break;
                if (*p == '"') { free(value); fields_drop(fields, length); return false; }
                value[value_length++] = *p++;
            }
            if (value_length + 1 == value_capacity) {
                value_capacity *= 2;
                char *grown = realloc(value, value_capacity);
                if (grown == NULL) { free(value); fields_drop(fields, length); return false; }
                value = grown;
            }
        }
        if (quoted) { free(value); fields_drop(fields, length); return false; }
        value[value_length] = '\0';
        if (length == capacity) {
            size_t next = capacity == 0 ? 8 : capacity * 2;
            char **grown = realloc(fields, next * sizeof(*grown));
            if (grown == NULL) { free(value); fields_drop(fields, length); return false; }
            fields = grown; capacity = next;
        }
        fields[length++] = value;
        if (*p == '\0') break;
        p++;
        if (*p == '\0') {
            char *empty = strdup("");
            if (empty == NULL) { fields_drop(fields, length); return false; }
            if (length == capacity) {
                size_t next = capacity * 2;
                char **grown = realloc(fields, next * sizeof(*grown));
                if (grown == NULL) { free(empty); fields_drop(fields, length); return false; }
                fields = grown; capacity = next;
            }
            fields[length++] = empty;
            break;
        }
    }
    *out = fields; *count = length; return true;
}

static CsvError aggregate(FILE *input, char delimiter, Totals *totals) {
    char *line = NULL; size_t capacity = 0; ssize_t length;
    length = getline(&line, &capacity, input);
    if (length < 0) { free(line); return feof(input) ? CSV_MISSING_HEADER : CSV_READ_ERROR; }
    if (length > 0 && line[length - 1] == '\n') line[--length] = '\0';
    if (length > 0 && line[length - 1] == '\r') line[--length] = '\0';
    if (!valid_utf8((const unsigned char *)line, (size_t)length)) { free(line); return CSV_INVALID_UTF8; }
    char **header = NULL; size_t header_count = 0;
    bool header_ok = parse_csv_fields(line, delimiter, &header, &header_count);
    const char *expected[] = {"date", "product", "region", "quantity", "unit_price_cents"};
    if (!header_ok || header_count != 5) { fields_drop(header, header_count); free(line); return CSV_INVALID_HEADER; }
    for (size_t i = 0; i < 5 && header_ok; ++i) header_ok = strcmp(header[i], expected[i]) == 0;
    fields_drop(header, header_count);
    if (!header_ok) { free(line); return CSV_INVALID_HEADER; }

    CsvError error = CSV_OK;
    while ((length = getline(&line, &capacity, input)) >= 0) {
        if (length > 0 && line[length - 1] == '\n') line[--length] = '\0';
        if (length > 0 && line[length - 1] == '\r') line[--length] = '\0';
        if (!valid_utf8((const unsigned char *)line, (size_t)length)) { error = CSV_INVALID_UTF8; break; }
        totals->total++;
        char **fields = NULL; size_t count = 0;
        if (!parse_csv_fields(line, delimiter, &fields, &count) || count != 5 ||
            fields[0][0] == '\0' || fields[1][0] == '\0' || fields[2][0] == '\0') {
            fields_drop(fields, count); totals->invalid++; continue;
        }
        uint64_t quantity = 0, price = 0, revenue = 0, next_quantity = 0, next_revenue = 0;
        bool numbers_ok = parse_uint64(fields[3], &quantity) && parse_uint64(fields[4], &price);
        bool arithmetic_ok = numbers_ok && checked_multiply(quantity, price, &revenue) &&
                             checked_add(totals->quantity, quantity, &next_quantity) &&
                             checked_add(totals->revenue, revenue, &next_revenue);
        if (!arithmetic_ok) {
            fields_drop(fields, count);
            if (numbers_ok && (!checked_multiply(quantity, price, &revenue) ||
                !checked_add(totals->quantity, quantity, &next_quantity) ||
                !checked_add(totals->revenue, revenue, &next_revenue))) {
                error = !checked_multiply(quantity, price, &revenue) ? CSV_REVENUE_OVERFLOW :
                        !checked_add(totals->quantity, quantity, &next_quantity) ? CSV_QUANTITY_OVERFLOW : CSV_REVENUE_OVERFLOW;
                break;
            }
            totals->invalid++; continue;
        }
        error = counter_increment(&totals->products, fields[1], revenue);
        if (error == CSV_OK) error = counter_increment(&totals->regions, fields[2], revenue);
        fields_drop(fields, count);
        if (error != CSV_OK) break;
        totals->quantity = next_quantity; totals->revenue = next_revenue; totals->valid++;
    }
    if (error == CSV_OK && ferror(input)) error = CSV_READ_ERROR;
    free(line); return error;
}

static void print_report(const Totals *totals) {
    printf("total=%" PRIu64 "\nvalid=%" PRIu64 "\ninvalid=%" PRIu64 "\nquantity=%" PRIu64 "\nrevenue_cents=%" PRIu64 "\n",
           totals->total, totals->valid, totals->invalid, totals->quantity, totals->revenue);
    for (size_t i = 0; i < totals->products.length; ++i)
        printf("product %s=%" PRIu64 "\n", totals->products.entries[i].name, totals->products.entries[i].value);
    for (size_t i = 0; i < totals->regions.length; ++i)
        printf("region %s=%" PRIu64 "\n", totals->regions.entries[i].name, totals->regions.entries[i].value);
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "missing input path\n"); return 2; }
    char delimiter = ',';
    for (int i = 2; i < argc; ++i) {
        if (strcmp(argv[i], "--delimiter") == 0 && i + 1 < argc && argv[i + 1][0] != '\0' && argv[i + 1][1] == '\0')
            delimiter = argv[++i][0];
        else { fprintf(stderr, "invalid option\n"); return 2; }
    }
    FILE *input = fopen(argv[1], "rb");
    if (input == NULL) { fprintf(stderr, "ReadError %s\n", strerror(errno)); return 1; }
    Totals totals = {0};
    CsvError error = aggregate(input, delimiter, &totals);
    int close_status = fclose(input);
    if (error == CSV_OK && close_status != 0) error = CSV_READ_ERROR;
    if (error != CSV_OK) {
        fprintf(stderr, "application error %d\n", error);
        counter_drop(&totals.products); counter_drop(&totals.regions); return 1;
    }
    print_report(&totals);
    counter_drop(&totals.products); counter_drop(&totals.regions); return 0;
}
