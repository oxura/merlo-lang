/* Readable C reference for the frozen NDJSON reporting algorithm. */
#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define U64_MAX_VALUE UINT64_MAX

typedef enum {
    APP_OK = 0,
    APP_INVALID_UTF8,
    APP_READ_ERROR,
    APP_INVALID_OPTION,
    APP_DURATION_OVERFLOW,
    APP_COUNT_OVERFLOW,
} AppError;

typedef struct {
    char *name;
    uint64_t value;
} CounterEntry;

typedef struct {
    CounterEntry *entries;
    size_t length;
    size_t capacity;
} Counter;

typedef struct {
    const char *level;
    const char *service;
    const char *contains;
    bool has_minimum;
    uint64_t minimum_duration_ms;
} Options;

typedef struct {
    uint64_t total, valid, invalid, matching;
    uint64_t duration_sum, duration_count, duration_max;
    Counter levels, services;
} Report;

static void counter_drop(Counter *counter) {
    for (size_t i = 0; i < counter->length; ++i) free(counter->entries[i].name);
    free(counter->entries);
    counter->entries = NULL;
    counter->length = 0;
    counter->capacity = 0;
}

static AppError counter_increment(Counter *counter, const char *name) {
    for (size_t i = 0; i < counter->length; ++i) {
        if (strcmp(counter->entries[i].name, name) == 0) {
            if (counter->entries[i].value == U64_MAX_VALUE) return APP_COUNT_OVERFLOW;
            counter->entries[i].value++;
            return APP_OK;
        }
    }
    if (counter->length == counter->capacity) {
        size_t next = counter->capacity == 0 ? 8 : counter->capacity * 2;
        CounterEntry *grown = realloc(counter->entries, next * sizeof(*grown));
        if (grown == NULL) return APP_READ_ERROR;
        counter->entries = grown;
        counter->capacity = next;
    }
    counter->entries[counter->length].name = strdup(name);
    if (counter->entries[counter->length].name == NULL) return APP_READ_ERROR;
    counter->entries[counter->length].value = 1;
    counter->length++;
    return APP_OK;
}

static AppError checked_add(uint64_t left, uint64_t right, uint64_t *result,
                            AppError overflow) {
    if (left > U64_MAX_VALUE - right) return overflow;
    *result = left + right;
    return APP_OK;
}

static bool valid_utf8(const unsigned char *text, size_t length) {
    for (size_t i = 0; i < length;) {
        unsigned char first = text[i];
        size_t needed = 0;
        uint32_t scalar = 0;
        uint32_t minimum = 0;
        if (first <= 0x7f) {
            i++;
            continue;
        } else if (first >= 0xc2 && first <= 0xdf) {
            needed = 1; scalar = first & 0x1f; minimum = 0x80;
        } else if (first >= 0xe0 && first <= 0xef) {
            needed = 2; scalar = first & 0x0f; minimum = 0x800;
        } else if (first >= 0xf0 && first <= 0xf4) {
            needed = 3; scalar = first & 0x07; minimum = 0x10000;
        } else return false;
        if (needed > length - i - 1) return false;
        for (size_t part = 1; part <= needed; ++part) {
            unsigned char continuation = text[i + part];
            if (continuation < 0x80 || continuation > 0xbf) return false;
            scalar = (scalar << 6) | (continuation & 0x3f);
        }
        if (scalar < minimum || scalar > 0x10ffff ||
            (scalar >= 0xd800 && scalar <= 0xdfff)) return false;
        i += needed + 1;
    }
    return true;
}

static const char *skip_space(const char *cursor) {
    while (*cursor == ' ' || *cursor == '\t' || *cursor == '\r' || *cursor == '\n') cursor++;
    return cursor;
}

/* Decode one JSON string into caller-owned storage. */
static bool json_string(const char **cursor, char *out, size_t capacity) {
    const char *p = skip_space(*cursor);
    if (*p++ != '"') return false;
    size_t used = 0;
    while (*p != '\0' && *p != '"') {
        unsigned char byte = (unsigned char)*p++;
        if (byte < 0x20 || byte == '\\') {
            if (byte != '\\') return false;
            unsigned char escaped = (unsigned char)*p++;
            const char *escapes = "\"\\/bfnrt";
            const char *found = strchr(escapes, escaped);
            if (found == NULL) return false;
            byte = escaped == 'b' ? '\b' : escaped == 'f' ? '\f' : escaped == 'n' ? '\n' :
                   escaped == 'r' ? '\r' : escaped == 't' ? '\t' : escaped;
        }
        if (used + 1 >= capacity) return false;
        out[used++] = (char)byte;
    }
    if (*p++ != '"') return false;
    out[used] = '\0';
    *cursor = p;
    return true;
}

static bool json_field_string(const char *object, const char *field,
                              char *out, size_t capacity) {
    size_t field_length = strlen(field);
    const char *p = object;
    while ((p = strchr(p, '"')) != NULL) {
        const char *key_start = p++;
        const char *key_end = strchr(p, '"');
        if (key_end == NULL) return false;
        if ((size_t)(key_end - p) == field_length && strncmp(p, field, field_length) == 0) {
            const char *value = skip_space(key_end + 1);
            if (*value++ != ':') return false;
            value = skip_space(value);
            return json_string(&value, out, capacity);
        }
        p = key_start + 1;
    }
    return false;
}

static bool json_field_uint64(const char *object, const char *field,
                              uint64_t *result, bool *present, bool *is_null) {
    size_t field_length = strlen(field);
    const char *p = object;
    *present = false;
    *is_null = false;
    while ((p = strchr(p, '"')) != NULL) {
        const char *key = ++p;
        const char *end = strchr(key, '"');
        if (end == NULL) return false;
        if ((size_t)(end - key) == field_length && strncmp(key, field, field_length) == 0) {
            const char *value = skip_space(end + 1);
            if (*value++ != ':') return false;
            value = skip_space(value);
            if (strncmp(value, "null", 4) == 0) {
                *present = true;
                *is_null = true;
                return true;
            }
            if (!isdigit((unsigned char)*value)) return false;
            uint64_t parsed = 0;
            do {
                unsigned digit = (unsigned)(*value++ - '0');
                if (parsed > (U64_MAX_VALUE - digit) / 10) return false;
                parsed = parsed * 10 + digit;
            } while (isdigit((unsigned char)*value));
            *result = parsed;
            *present = true;
            return true;
        }
        p = end + 1;
    }
    return true;
}

static bool parse_event(const char *line, char *level, size_t level_cap,
                        char *service, size_t service_cap, char *message,
                        size_t message_cap, uint64_t *duration, bool *has_duration) {
    const char *start = skip_space(line);
    size_t length = strlen(start);
    if (length < 2 || start[0] != '{' || start[length - 1] != '}') return false;
    char timestamp[4096];
    if (!json_field_string(start, "timestamp", timestamp, sizeof(timestamp))) return false;
    if (!json_field_string(start, "level", level, level_cap)) return false;
    if (!json_field_string(start, "service", service, service_cap)) return false;
    if (!json_field_string(start, "message", message, message_cap)) return false;
    bool present = false;
    bool is_null = false;
    if (!json_field_uint64(start, "duration_ms", duration, &present, &is_null)) return false;
    *has_duration = present && !is_null;
    return true;
}


static bool contains_text(const char *line, const char *needle) {
    return strstr(line, needle) != NULL;
}

static AppError analyze(FILE *input, const Options *options, Report *report) {
    char *line = NULL;
    size_t capacity = 0;
    AppError error = APP_OK;
    char level[4096], service[4096], message[65536];
    while (getline(&line, &capacity, input) >= 0) {
        size_t length = strlen(line);
        if (length > 0 && line[length - 1] == '\n') line[--length] = '\0';
        if (length > 0 && line[length - 1] == '\r') line[--length] = '\0';
        report->total++;
        if (!valid_utf8((const unsigned char *)line, length)) { error = APP_INVALID_UTF8; break; }
        uint64_t duration = 0; bool has_duration = false;
        if (!parse_event(line, level, sizeof(level), service, sizeof(service),
                         message, sizeof(message), &duration, &has_duration)) {
            report->invalid++;
            continue;
        }
        report->valid++;
        if (options->level != NULL && strcmp(level, options->level) != 0) continue;
        if (options->service != NULL && strcmp(service, options->service) != 0) continue;
        if (options->contains != NULL && !contains_text(message, options->contains)) continue;
        if (options->has_minimum && (!has_duration || duration < options->minimum_duration_ms)) continue;
        report->matching++;
        error = counter_increment(&report->levels, level);
        if (error != APP_OK) break;
        error = counter_increment(&report->services, service);
        if (error != APP_OK) break;
        if (has_duration) {
            error = checked_add(report->duration_sum, duration, &report->duration_sum,
                                APP_DURATION_OVERFLOW);
            if (error != APP_OK) break;
            report->duration_count++;
            if (duration > report->duration_max) report->duration_max = duration;
        }
    }
    if (ferror(input) && error == APP_OK) error = APP_READ_ERROR;
    free(line);
    return error;
}

static void print_report(const Report *report) {
    uint64_t average = report->duration_count == 0 ? 0 : report->duration_sum / report->duration_count;
    printf("total=%" PRIu64 "\nvalid=%" PRIu64 "\ninvalid=%" PRIu64 "\nmatching=%" PRIu64 "\n",
           report->total, report->valid, report->invalid, report->matching);
    printf("duration_sum_ms=%" PRIu64 "\nduration_average_ms=%" PRIu64 "\nduration_max_ms=%" PRIu64 "\n",
           report->duration_sum, average, report->duration_max);
    for (size_t i = 0; i < report->levels.length; ++i)
        printf("level %s=%" PRIu64 "\n", report->levels.entries[i].name, report->levels.entries[i].value);
    for (size_t i = 0; i < report->services.length; ++i)
        printf("service %s=%" PRIu64 "\n", report->services.entries[i].name, report->services.entries[i].value);
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "missing input path\n"); return 2; }
    Options options = {0};
    const char *path = argv[1];
    for (int i = 2; i < argc; ++i) {
        if (strcmp(argv[i], "--level") == 0 && i + 1 < argc) options.level = argv[++i];
        else if (strcmp(argv[i], "--service") == 0 && i + 1 < argc) options.service = argv[++i];
        else if (strcmp(argv[i], "--contains") == 0 && i + 1 < argc) options.contains = argv[++i];
        else if (strcmp(argv[i], "--minimum-duration") == 0 && i + 1 < argc) {
            char *end = NULL; errno = 0;
            unsigned long long value = strtoull(argv[++i], &end, 10);
            if (errno != 0 || end == argv[i] || *end != '\0') { fprintf(stderr, "InvalidMinimumDuration\n"); return 2; }
            options.has_minimum = true; options.minimum_duration_ms = (uint64_t)value;
        } else { fprintf(stderr, "invalid option\n"); return 2; }
    }
    FILE *input = fopen(path, "rb");
    if (input == NULL) { fprintf(stderr, "ReadError %s\n", strerror(errno)); return 1; }
    Report report = {0};
    AppError error = analyze(input, &options, &report);
    int close_status = fclose(input);
    if (error == APP_OK && close_status != 0) error = APP_READ_ERROR;
    if (error != APP_OK) {
        fprintf(stderr, "application error %d\n", error);
        counter_drop(&report.levels); counter_drop(&report.services);
        return 1;
    }
    print_report(&report);
    counter_drop(&report.levels); counter_drop(&report.services);
    return 0;
}
