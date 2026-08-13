/* Readable C reference for the frozen grep-style text search algorithm. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef enum { SEARCH_OK = 0, SEARCH_INVALID_UTF8, SEARCH_READ_ERROR } SearchError;

typedef struct {
    uint64_t line_number;
    char *line;
} Match;

typedef struct {
    Match *items;
    size_t length;
    size_t capacity;
} Matches;

typedef struct {
    const char *contains;
    bool ignore_case;
    bool count_only;
} Options;

static void matches_drop(Matches *matches) {
    for (size_t i = 0; i < matches->length; ++i) free(matches->items[i].line);
    free(matches->items);
    *matches = (Matches){0};
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

static char *ascii_lower_copy(const char *text) {
    size_t length = strlen(text);
    char *copy = malloc(length + 1);
    if (copy == NULL) return NULL;
    for (size_t i = 0; i < length; ++i)
        copy[i] = text[i] >= 'A' && text[i] <= 'Z' ? (char)(text[i] + ('a' - 'A')) : text[i];
    copy[length] = '\0';
    return copy;
}

static SearchError search_file(FILE *input, const Options *options, Matches *matches,
                               uint64_t *total_lines) {
    char *line = NULL; size_t capacity = 0; ssize_t length; uint64_t line_number = 0;
    char *needle = options->ignore_case ? ascii_lower_copy(options->contains) : strdup(options->contains);
    if (needle == NULL) return SEARCH_READ_ERROR;
    while ((length = getline(&line, &capacity, input)) >= 0) {
        if (length > 0 && line[length - 1] == '\n') line[--length] = '\0';
        if (length > 0 && line[length - 1] == '\r') line[--length] = '\0';
        if (!valid_utf8((const unsigned char *)line, (size_t)length)) {
            free(needle); free(line); return SEARCH_INVALID_UTF8;
        }
        if (line_number == UINT64_MAX) { free(needle); free(line); return SEARCH_READ_ERROR; }
        line_number++;
        char *haystack = options->ignore_case ? ascii_lower_copy(line) : strdup(line);
        if (haystack == NULL) { free(needle); free(line); return SEARCH_READ_ERROR; }
        if (strstr(haystack, needle) != NULL) {
            if (matches->length == matches->capacity) {
                size_t next = matches->capacity == 0 ? 16 : matches->capacity * 2;
                Match *grown = realloc(matches->items, next * sizeof(*grown));
                if (grown == NULL) { free(haystack); free(needle); free(line); return SEARCH_READ_ERROR; }
                matches->items = grown; matches->capacity = next;
            }
            matches->items[matches->length++] = (Match){line_number, strdup(line)};
            if (matches->items[matches->length - 1].line == NULL) {
                free(haystack); free(needle); free(line); return SEARCH_READ_ERROR;
            }
        }
        free(haystack);
    }
    if (ferror(input)) { free(needle); free(line); return SEARCH_READ_ERROR; }
    *total_lines = line_number;
    free(needle); free(line); return SEARCH_OK;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "missing input path\n"); return 2; }
    Options options = {0}; const char *path = argv[1];
    for (int i = 2; i < argc; ++i) {
        if (strcmp(argv[i], "--contains") == 0 && i + 1 < argc) options.contains = argv[++i];
        else if (strcmp(argv[i], "--ignore-case") == 0) options.ignore_case = true;
        else if (strcmp(argv[i], "--count") == 0) options.count_only = true;
        else { fprintf(stderr, "invalid option\n"); return 2; }
    }
    if (options.contains == NULL) { fprintf(stderr, "MissingContains\n"); return 1; }
    FILE *input = fopen(path, "rb");
    if (input == NULL) { fprintf(stderr, "ReadError %s\n", strerror(errno)); return 1; }
    Matches matches = {0}; uint64_t total_lines = 0;
    SearchError error = search_file(input, &options, &matches, &total_lines);
    int close_status = fclose(input);
    if (error == SEARCH_OK && close_status != 0) error = SEARCH_READ_ERROR;
    if (error != SEARCH_OK) {
        fprintf(stderr, "application error %d\n", error);
        matches_drop(&matches); return 1;
    }
    if (options.count_only) printf("%zu\n", matches.length);
    else for (size_t i = 0; i < matches.length; ++i)
        printf("%" PRIu64 ":%s\n", matches.items[i].line_number, matches.items[i].line);
    matches_drop(&matches);
    return 0;
}