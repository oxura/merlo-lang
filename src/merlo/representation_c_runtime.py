"""C runtime and ownership glue emission for the representation backend."""

from __future__ import annotations

from merlo.representation_c_types import _c_name, _identifier, _is_owner
from merlo.representation_ir import TypeDescriptor


class RuntimeEmissionMixin:
    """Emission methods for generated runtime, effects, files, and ownership glue.

    The mixin deliberately relies on the emitter's existing state and expression
    lowering methods; it owns only these runtime sections' source authority.
    """
    def _primitive_runtime(self) -> str:
        return """static uint8_t merlo_bytes_load(const MerloBytesView *view, uint64_t index) {
    if (index >= view->length) merlo_bounds_trap(index, view->length);
    return view->data[index];
}

static uint8_t merlo_text_view_load(const MerloTextView *view, uint64_t index) {
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

static MerloText merlo_text_from_view(const MerloTextView *view) {
    MerloText result = { NULL, view->length };
    if (result.length != 0) {
        result.data = (uint8_t *)malloc((size_t)result.length);
        if (result.data == NULL) merlo_allocation_trap();
        memcpy(result.data, view->data, (size_t)result.length);
        ++merlo_allocations;
        ++merlo_text_allocations;
        merlo_bytes_copied += result.length;
    }
    return result;
}
static MerloText merlo_text_clone(const MerloText *text) {
    MerloTextView view = merlo_text_as_view(text);
    return merlo_text_from_view(&view);
}
static MerloBytes merlo_bytes_concat(MerloBytes left, MerloBytes right) {
    if (right.length > UINT64_MAX - left.length) merlo_allocation_trap();
    MerloBytes result = { NULL, left.length + right.length };
    if (result.length != 0) {
        result.data = (uint8_t *)malloc((size_t)result.length);
        if (result.data == NULL) merlo_allocation_trap();
        if (left.length) memcpy(result.data, left.data, (size_t)left.length);
        if (right.length) memcpy(result.data + left.length, right.data, (size_t)right.length);
        ++merlo_allocations;
        merlo_bytes_copied += result.length;
    }
    return result;
}
static MerloText merlo_text_concat(MerloText left, MerloText right) {
    if (right.length > UINT64_MAX - left.length) merlo_allocation_trap();
    MerloText result = { NULL, left.length + right.length };
    if (result.length != 0) {
        result.data = (uint8_t *)malloc((size_t)result.length);
        if (result.data == NULL) merlo_allocation_trap();
        if (left.length) memcpy(result.data, left.data, (size_t)left.length);
        if (right.length) memcpy(result.data + left.length, right.data, (size_t)right.length);
        ++merlo_allocations;
        ++merlo_text_allocations;
        merlo_bytes_copied += result.length;
    }
    return result;
}

static MerloTextView merlo_text_view_slice_bytes(
    const MerloTextView *view, uint64_t start, uint64_t length
) {
    if (start > view->length || length > view->length - start) {
        merlo_bounds_trap(start, view->length);
    }
    const uint8_t *data = view->data;
    if (start != 0) data += start;
    MerloTextView result = { data, length };
    return result;
}

static MerloText merlo_text_from_view_slice(
    const MerloTextView *view, uint64_t start, uint64_t length
) {
    MerloTextView slice = merlo_text_view_slice_bytes(view, start, length);
    return merlo_text_from_view(&slice);
}



static MerloText merlo_text_literal(const uint8_t *data, uint64_t length) {
    MerloText result = { NULL, length };
    if (length != 0) {
        result.data = (uint8_t *)malloc((size_t)length);
        if (result.data == NULL) merlo_allocation_trap();
        memcpy(result.data, data, (size_t)length);
        ++merlo_allocations;
        ++merlo_text_allocations;
        merlo_bytes_copied += length;
    }
    return result;
}
static MerloBytes merlo_bytes_literal(const uint8_t *data, uint64_t length) {
    MerloBytes result = { NULL, length };
    if (length != 0) {
        result.data = (uint8_t *)malloc((size_t)length);
        if (result.data == NULL) merlo_allocation_trap();
        memcpy(result.data, data, (size_t)length);
        ++merlo_allocations;
        merlo_bytes_copied += length;
    }
    return result;
}
static bool merlo_text_equal_values(MerloText left, MerloText right) {
    return left.length == right.length
        && (left.length == 0
            || memcmp(left.data, right.data, (size_t)left.length) == 0);
}

static uint8_t merlo_ascii_lower(uint8_t byte) {
    return byte >= 'A' && byte <= 'Z' ? (uint8_t)(byte + ('a' - 'A')) : byte;
}

static bool merlo_text_view_contains(
    const MerloTextView *haystack, const MerloText *needle, bool ignore_case
) {
    if (needle->length == 0) return true;
    if (needle->length > haystack->length) return false;
    for (uint64_t start = 0; start <= haystack->length - needle->length; ++start) {
        bool matched = true;
        for (uint64_t index = 0; index < needle->length; ++index) {
            uint8_t left = haystack->data[start + index];
            uint8_t right = needle->data[index];
            if (ignore_case) {
                left = merlo_ascii_lower(left);
                right = merlo_ascii_lower(right);
            }
            if (left != right) { matched = false; break; }
        }
        if (matched) return true;
    }
    return false;
}
static bool merlo_text_view_prefix_suffix(
    const MerloTextView *haystack, const MerloText *needle, bool suffix
) {
    if (needle->length > haystack->length) return false;
    uint64_t start = suffix ? haystack->length - needle->length : 0;
    for (uint64_t index = 0; index < needle->length; ++index) {
        if (haystack->data[start + index] != needle->data[index]) return false;
    }
    return true;
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
    if (capacity < 32) capacity = 32;
    if (capacity > (uint64_t)SIZE_MAX || capacity > (uint64_t)PTRDIFF_MAX) {
        merlo_overflow_trap("TextBuilderCapacity");
    }
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
static void merlo_text_builder_append_text(MerloTextBuilder *builder, const MerloText *text) {
    merlo_text_builder_reserve(builder, text->length);
    if (text->length != 0) memcpy(builder->data + builder->length, text->data, (size_t)text->length);
    builder->length += text->length;
}

static void merlo_text_builder_append_uint64(MerloTextBuilder *builder, uint64_t value) {
    uint8_t digits[20];
    uint64_t length = 0;
    do {
        digits[length++] = (uint8_t)('0' + value % 10);
        value /= 10;
    } while (value != 0);
    merlo_text_builder_reserve(builder, length);
    while (length != 0) builder->data[builder->length++] = digits[--length];
}

static MerloText merlo_text_builder_finish(MerloTextBuilder *builder) {
    MerloText result = { builder->data, builder->length };
    if (builder->data != NULL) ++merlo_text_allocations;
    builder->data = NULL;
    builder->length = 0;
    builder->capacity = 0;
    return result;
}

static bool merlo_valid_utf8(const uint8_t *data, uint64_t length) {
    for (uint64_t i = 0; i < length;) {
        uint8_t first = data[i++];
        uint64_t width = first < 0x80 ? 1 :
            first >= 0xc2 && first <= 0xdf ? 2 :
            first >= 0xe0 && first <= 0xef ? 3 :
            first >= 0xf0 && first <= 0xf4 ? 4 : 0;
        if (width == 0 || i + width - 1 > length) return false;
        if (width >= 3) {
            uint8_t second = data[i];
            if ((first == 0xe0 && second < 0xa0)
                    || (first == 0xed && second > 0x9f)
                    || (first == 0xf0 && second < 0x90)
                    || (first == 0xf4 && second > 0x8f)) {
                return false;
            }
        }
        for (uint64_t j = 1; j < width; ++j) {
            if ((data[i++] & 0xc0) != 0x80) return false;
        }
    }
    return true;
}"""

    def _effect_runtime(self) -> str:
        sections: list[str] = []
        if "console.read" in self.used_effects:
            sections.append(r'''static MerloBytes merlo_console_read(void) {
    merlo_require_capability(MERLO_EFFECT_CONSOLE_READ);
    MerloBytes result = { NULL, 0 };
    uint8_t chunk[4096];
    size_t count = fread(chunk, 1, sizeof(chunk), stdin);
    if (ferror(stdin)) return result;
    if (count != 0) {
        result.data = (uint8_t *)malloc(count);
        if (result.data == NULL) merlo_allocation_trap();
        memcpy(result.data, chunk, count);
        result.length = (uint64_t)count;
        ++merlo_allocations;
    }
    return result;
}
static MerloText merlo_console_read_line(void) {
    merlo_require_capability(MERLO_EFFECT_CONSOLE_READ);
    char *line = NULL;
    size_t capacity = 0;
    ssize_t length = getline(&line, &capacity, stdin);
    if (length < 0) {
        free(line);
        return (MerloText){ NULL, 0 };
    }
    if (!merlo_valid_utf8((const uint8_t *)line, (uint64_t)length)) {
        free(line);
        fputs("InvalidUtf8\n", stderr);
        abort();
    }
    MerloText result = merlo_text_literal(
        (const uint8_t *)line, (uint64_t)length
    );
    free(line);
    return result;
}
static MerloText merlo_console_read_all(void) {
    merlo_require_capability(MERLO_EFFECT_CONSOLE_READ);
    uint8_t *data = NULL;
    size_t used = 0;
    size_t capacity = 0;
    uint8_t chunk[4096];
    while (!feof(stdin)) {
        size_t count = fread(chunk, 1, sizeof(chunk), stdin);
        if (ferror(stdin)) {
            free(data);
            return (MerloText){ NULL, 0 };
        }
        if (count == 0) break;
        if (used > SIZE_MAX - count) merlo_allocation_trap();
        size_t required = used + count;
        if (required > capacity) {
            size_t next = capacity == 0 ? 4096 : capacity;
            while (next < required) {
                if (next > SIZE_MAX / 2) {
                    next = required;
                    break;
                }
                next *= 2;
            }
            uint8_t *grown = (uint8_t *)realloc(data, next);
            if (grown == NULL) {
                free(data);
                merlo_allocation_trap();
            }
            data = grown;
            capacity = next;
        }
        memcpy(data + used, chunk, count);
        used += count;
    }
    if (!merlo_valid_utf8(data, (uint64_t)used)) {
        free(data);
        fputs("InvalidUtf8\n", stderr);
        abort();
    }
    MerloText result = merlo_text_literal(data, (uint64_t)used);
    free(data);
    return result;
}''')
        if "console.write" in self.used_effects:
            sections.append(r'''static void merlo_console_write_view(MerloTextView value) {
    merlo_require_capability(MERLO_EFFECT_CONSOLE_WRITE);
    if (value.length != 0) {
        fwrite(value.data, 1, (size_t)value.length, stdout);
    }
}
static void merlo_console_write(const MerloText *value) {
    merlo_console_write_view(merlo_text_as_view(value));
}''')
        if "clock.now" in self.used_effects:
            sections.append(r'''static uint64_t merlo_clock_now(void) {
    merlo_require_capability(MERLO_EFFECT_CLOCK_NOW);
    return (uint64_t)time(NULL);
}''')
        if "random.read" in self.used_effects:
            sections.append(r'''static MerloBytes merlo_random_read(uint64_t length) {
    merlo_require_capability(MERLO_EFFECT_RANDOM_READ);
    MerloBytes result = { NULL, length };
    if (length != 0) {
        result.data = (uint8_t *)malloc((size_t)length);
        if (result.data == NULL) merlo_allocation_trap();
        ssize_t received = 0;
        while ((uint64_t)received < length) {
            ssize_t count = getrandom(
                result.data + received, (size_t)(length - (uint64_t)received), 0
            );
            if (count > 0) {
                received += count;
                continue;
            }
            if (count < 0 && errno == EINTR) continue;
            free(result.data);
            result.data = NULL;
            result.length = 0;
            return result;
        }
        ++merlo_allocations;
    }
    return result;
}''')
        if "env.read" in self.used_effects:
            sections.append(r'''static MerloText merlo_env_read(const MerloText *key) {
    merlo_require_capability(MERLO_EFFECT_ENV_READ);
    if (merlo_capabilities.environment_keys == NULL) return (MerloText){ NULL, 0 };
    char *name = (char *)malloc((size_t)key->length + 1);
    if (name == NULL) merlo_allocation_trap();
    memcpy(name, key->data, (size_t)key->length);
    name[key->length] = '\0';
    if (!merlo_allowlist_contains(merlo_capabilities.environment_keys, name)) {
        free(name);
        return (MerloText){ NULL, 0 };
    }
    const char *value = getenv(name);
    free(name);
    if (value == NULL) return (MerloText){ NULL, 0 };
    return merlo_text_literal((const uint8_t *)value, (uint64_t)strlen(value));
}''')
        if "process.args" in self.used_effects:
            sections.append(r'''static int merlo_runtime_argc = 0;
static char **merlo_runtime_argv = NULL;
static uint64_t merlo_process_args_count(void) {
    merlo_require_capability(MERLO_EFFECT_PROCESS_ARGS);
    return merlo_runtime_argc > 0 ? (uint64_t)merlo_runtime_argc - 1u : 0u;
}
static MerloText merlo_process_arg(uint64_t index) {
    merlo_require_capability(MERLO_EFFECT_PROCESS_ARGS);
    uint64_t count = merlo_process_args_count();
    if (index >= count || merlo_runtime_argv == NULL) {
        return (MerloText){ NULL, 0 };
    }
    const char *value = merlo_runtime_argv[index + 1u];
    return merlo_text_literal(
        (const uint8_t *)value, (uint64_t)strlen(value)
    );
}''')
        network_effects = self.used_effects & {"network.tcp", "network.http"}
        if network_effects:
            sections.append(r'''static int merlo_connect_host(const char *host, uint16_t port) {
    struct addrinfo hints = {0};
    struct addrinfo *result = NULL;
    char service[8];
    snprintf(service, sizeof(service), "%u", (unsigned)port);
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_family = AF_UNSPEC;
    if (getaddrinfo(host, service, &hints, &result) != 0) return -1;
    int descriptor = -1;
    for (struct addrinfo *item = result; item != NULL; item = item->ai_next) {
        descriptor = socket(item->ai_family, item->ai_socktype, item->ai_protocol);
        if (descriptor < 0) continue;
        if (connect(descriptor, item->ai_addr, item->ai_addrlen) == 0) break;
        close(descriptor);
        descriptor = -1;
    }
    freeaddrinfo(result);
    return descriptor;
}''')
            sections.append("static uint32_t merlo_network_error = 0;")
        if "network.tcp" in self.used_effects:
            sections.append(r'''
static uint64_t merlo_network_tcp_guard(void) {
    merlo_require_capability(MERLO_EFFECT_NETWORK_TCP);
    if (merlo_capabilities.network_host == NULL) return 1;
    int descriptor = merlo_connect_host(merlo_capabilities.network_host, 80);
    if (descriptor < 0) return 1;
    close(descriptor);
    return 0;
}
static uint64_t merlo_network_tcp_connect(const MerloText *host, uint64_t port) {
    merlo_require_capability(MERLO_EFFECT_NETWORK_TCP);
    merlo_network_error = 0;
    if (host == NULL || merlo_capabilities.network_host == NULL) {
        merlo_network_error = 1;
        return UINT64_MAX;
    }
    char *name = (char *)malloc((size_t)host->length + 1);
    if (name == NULL) merlo_allocation_trap();
    memcpy(name, host->data, (size_t)host->length);
    name[host->length] = '\0';
    if (!merlo_allowlist_contains(merlo_capabilities.network_host, name)) {
        free(name);
        merlo_network_error = 1;
        return UINT64_MAX;
    }
    int descriptor = merlo_connect_host(name, (uint16_t)port);
    free(name);
    if (descriptor < 0) merlo_network_error = 1;
    return descriptor < 0 ? UINT64_MAX : (uint64_t)descriptor;
}
static uint64_t merlo_network_tcp_send(uint64_t handle, const MerloBytesView *data) {
    merlo_require_capability(MERLO_EFFECT_NETWORK_TCP);
    merlo_network_error = 0;
    if (handle == UINT64_MAX || data == NULL) {
        merlo_network_error = 1;
        return 0;
    }
    ssize_t sent = send((int)handle, data->data, (size_t)data->length, 0);
    if (sent < 0) merlo_network_error = 1;
    return sent < 0 ? 0 : (uint64_t)sent;
}
static MerloBytes merlo_network_tcp_receive(uint64_t handle, uint64_t limit) {
    merlo_require_capability(MERLO_EFFECT_NETWORK_TCP);
    merlo_network_error = 0;
    MerloBytes result = { NULL, 0 };
    if (handle == UINT64_MAX || limit == 0) {
        if (handle == UINT64_MAX) merlo_network_error = 1;
        return result;
    }
    result.data = (uint8_t *)malloc((size_t)limit);
    if (result.data == NULL) merlo_allocation_trap();
    ssize_t count = recv((int)handle, result.data, (size_t)limit, 0);
    if (count <= 0) {
        free(result.data);
        result.data = NULL;
        merlo_network_error = 1;
        return result;
    }
    result.length = (uint64_t)count;
    ++merlo_allocations;
    return result;
}
static uint64_t merlo_network_tcp_close(uint64_t handle) {
    merlo_require_capability(MERLO_EFFECT_NETWORK_TCP);
    merlo_network_error = 0;
    if (handle == UINT64_MAX) {
        merlo_network_error = 1;
        return 1;
    }
    if (close((int)handle) != 0) {
        merlo_network_error = 1;
        return 1;
    }
    return 0;
}''')
        if "network.http" in self.used_effects:
            sections.append(r'''static MerloBytes merlo_network_http_request(const MerloText *url) {
    merlo_require_capability(MERLO_EFFECT_NETWORK_HTTP);
    merlo_network_error = 0;
    MerloBytes result = { NULL, 0 };
    static const uint8_t prefix[] = "http://";
    if (
        url == NULL
        || url->length <= sizeof(prefix) - 1
        || memcmp(url->data, prefix, sizeof(prefix) - 1) != 0
        || merlo_capabilities.network_host == NULL
    ) {
        merlo_network_error = 1;
        return result;
    }
    uint64_t authority_start = (uint64_t)(sizeof(prefix) - 1);
    uint64_t slash = authority_start;
    while (slash < url->length && url->data[slash] != (uint8_t)'/') ++slash;
    uint64_t authority_length = slash - authority_start;
    if (authority_length == 0 || authority_length > UINT16_MAX) {
        merlo_network_error = 1;
        return result;
    }
    char *authority = (char *)malloc((size_t)authority_length + 1u);
    if (authority == NULL) merlo_allocation_trap();
    memcpy(authority, url->data + authority_start, (size_t)authority_length);
    authority[authority_length] = '\0';
    char *separator = strrchr(authority, ':');
    uint16_t port = 80;
    size_t host_length = (size_t)authority_length;
    if (separator != NULL) {
        char *end = NULL;
        errno = 0;
        unsigned long parsed = strtoul(separator + 1, &end, 10);
        if (
            errno != 0
            || end == separator + 1
            || *end != '\0'
            || parsed == 0
            || parsed > UINT16_MAX
        ) {
            free(authority);
            merlo_network_error = 1;
            return result;
        }
        port = (uint16_t)parsed;
        host_length = (size_t)(separator - authority);
    }
    char *host = (char *)malloc(host_length + 1u);
    if (host == NULL) merlo_allocation_trap();
    memcpy(host, authority, host_length);
    host[host_length] = '\0';
    if (
        host_length == 0
        || !merlo_allowlist_contains(merlo_capabilities.network_host, host)
    ) {
        free(host);
        free(authority);
        merlo_network_error = 1;
        return result;
    }
    int descriptor = merlo_connect_host(host, port);
    free(host);
    if (descriptor < 0) {
        free(authority);
        merlo_network_error = 1;
        return result;
    }
    const uint8_t *path = slash < url->length
        ? url->data + slash
        : (const uint8_t *)"/";
    uint64_t path_length = slash < url->length ? url->length - slash : 1u;
    if (path_length > UINT16_MAX) {
        close(descriptor);
        free(authority);
        merlo_network_error = 1;
        return result;
    }
    size_t request_capacity = (size_t)path_length + (size_t)authority_length + 64u;
    char *request = (char *)malloc(request_capacity);
    if (request == NULL) merlo_allocation_trap();
    int request_length = snprintf(
        request,
        request_capacity,
        "GET %.*s HTTP/1.0\r\nHost: %s\r\nConnection: close\r\n\r\n",
        (int)path_length,
        (const char *)path,
        authority
    );
    free(authority);
    if (request_length < 0 || (size_t)request_length >= request_capacity) {
        free(request);
        close(descriptor);
        merlo_network_error = 1;
        return result;
    }
    size_t sent = 0;
    while (sent < (size_t)request_length) {
        ssize_t count = send(
            descriptor,
            request + sent,
            (size_t)request_length - sent,
            0
        );
        if (count > 0) {
            sent += (size_t)count;
            continue;
        }
        if (count < 0 && errno == EINTR) continue;
        free(request);
        close(descriptor);
        merlo_network_error = 1;
        return result;
    }
    free(request);
    size_t used = 0;
    size_t capacity = 0;
    uint8_t chunk[4096];
    for (;;) {
        ssize_t count = recv(descriptor, chunk, sizeof(chunk), 0);
        if (count == 0) break;
        if (count < 0 && errno == EINTR) continue;
        if (count < 0 || used > SIZE_MAX - (size_t)count) {
            free(result.data);
            close(descriptor);
            merlo_network_error = 1;
            return (MerloBytes){ NULL, 0 };
        }
        size_t required = used + (size_t)count;
        if (required > capacity) {
            size_t next = capacity == 0 ? 4096 : capacity;
            while (next < required) {
                if (next > SIZE_MAX / 2) {
                    next = required;
                    break;
                }
                next *= 2;
            }
            uint8_t *grown = (uint8_t *)realloc(result.data, next);
            if (grown == NULL) {
                free(result.data);
                close(descriptor);
                merlo_allocation_trap();
            }
            result.data = grown;
            capacity = next;
        }
        memcpy(result.data + used, chunk, (size_t)count);
        used = required;
    }
    close(descriptor);
    size_t first_space = 0;
    while (first_space < used && result.data[first_space] != (uint8_t)' ') {
        ++first_space;
    }
    if (
        first_space + 3 >= used
        || result.data[first_space + 1] < (uint8_t)'0'
        || result.data[first_space + 1] > (uint8_t)'9'
        || result.data[first_space + 2] < (uint8_t)'0'
        || result.data[first_space + 2] > (uint8_t)'9'
        || result.data[first_space + 3] < (uint8_t)'0'
        || result.data[first_space + 3] > (uint8_t)'9'
    ) {
        free(result.data);
        merlo_network_error = 1;
        return (MerloBytes){ NULL, 0 };
    }
    unsigned status =
        (unsigned)(result.data[first_space + 1] - (uint8_t)'0') * 100u
        + (unsigned)(result.data[first_space + 2] - (uint8_t)'0') * 10u
        + (unsigned)(result.data[first_space + 3] - (uint8_t)'0');
    size_t body = 0;
    while (
        body + 3 < used
        && !(
            result.data[body] == (uint8_t)'\r'
            && result.data[body + 1] == (uint8_t)'\n'
            && result.data[body + 2] == (uint8_t)'\r'
            && result.data[body + 3] == (uint8_t)'\n'
        )
    ) {
        ++body;
    }
    if (status < 200u || status >= 300u || body + 3 >= used) {
        free(result.data);
        merlo_network_error = 1;
        return (MerloBytes){ NULL, 0 };
    }
    body += 4;
    result.length = (uint64_t)(used - body);
    if (result.length == 0) {
        free(result.data);
        result.data = NULL;
    } else {
        memmove(result.data, result.data + body, (size_t)result.length);
        ++merlo_allocations;
    }
    return result;
}''')
        if "fs.write" in self.used_effects:
            sections.append(r'''static uint32_t merlo_file_write_error = 0;
static bool merlo_path_allowed(const MerloText *path);
static uint64_t merlo_file_write_all(const MerloText *path, const MerloBytesView *data) {
    merlo_require_capability(MERLO_EFFECT_FS_WRITE);
    merlo_file_write_error = 0;
    if (!merlo_path_allowed(path)) {
        merlo_file_write_error = 1;
        return 1;
    }
    char *name = (char *)malloc((size_t)path->length + 1);
    if (name == NULL) merlo_allocation_trap();
    memcpy(name, path->data, (size_t)path->length);
    name[path->length] = '\0';
    FILE *stream = fopen(name, "wb");
    free(name);
    if (stream == NULL) {
        merlo_file_write_error = 1;
        return 1;
    }
    size_t written = fwrite(data->data, 1, (size_t)data->length, stream);
    int close_status = fclose(stream);
    if (written != (size_t)data->length || close_status != 0) merlo_file_write_error = 1;
    return merlo_file_write_error;
}
static uint64_t merlo_file_write_text(const MerloText *path, const MerloTextView *data) {
    MerloBytesView bytes = { data->data, data->length };
    return merlo_file_write_all(path, &bytes);
}''')
        return "\n".join(sections)

    def _file_runtime(self) -> str:
        if (
            not self.used_effects & {"fs.read", "fs.write"}
            and "FileReader" not in self.descriptors
            and "FileWriter" not in self.descriptors
        ):
            return ""
        return r'''static uint32_t merlo_file_error = 0;
static uint64_t merlo_file_error_line = 0;
static bool merlo_path_allowed(const MerloText *path) {
    if (merlo_capabilities.filesystem_root == NULL) return false;
    size_t root_length = strlen(merlo_capabilities.filesystem_root);
    if (root_length == 0 || path->length < (uint64_t)root_length) return false;
    if (memcmp(path->data, merlo_capabilities.filesystem_root, root_length) != 0) return false;
    if (root_length == 1 && merlo_capabilities.filesystem_root[0] == '/') return true;
    return path->length == (uint64_t)root_length
        || path->data[root_length] == (uint8_t)'/';
}
static MerloBytes merlo_file_read_all(const MerloText *path);
static uint64_t merlo_file_close(MerloFileReader *reader) {
    uint64_t status = 0;
    if (reader == NULL) return 1;
    if (reader->stream != NULL) {
        if (fclose(reader->stream) != 0) {
            merlo_file_error = UINT32_C(5);
            status = 1;
        }
        reader->stream = NULL;
    }
    free(reader->buffer);
    reader->buffer = NULL;
    reader->buffer_length = 0;
    reader->buffer_capacity = 0;
    ++reader->generation;
    return status;
}
static uint64_t merlo_file_close_writer(MerloFileWriter *writer) {
    uint64_t status = 0;
    if (writer == NULL) return 1;
    if (writer->stream != NULL) {
        if (fclose(writer->stream) != 0) {
            merlo_file_error = UINT32_C(5);
            status = 1;
        }
        writer->stream = NULL;
    }
    ++writer->generation;
    return status;
}
static MerloFileWriter merlo_file_open_write(const MerloText *path) {
    merlo_require_capability(MERLO_EFFECT_FS_WRITE);
    merlo_file_error = 0;
    if (!merlo_path_allowed(path)) {
        merlo_file_error = UINT32_C(4);
        return (MerloFileWriter){ NULL, 1 };
    }
    MerloFileWriter result = { NULL, 1 };
    char *name = (char *)malloc((size_t)path->length + 1);
    if (name == NULL) merlo_allocation_trap();
    memcpy(name, path->data, (size_t)path->length);
    name[path->length] = '\0';
    result.stream = fopen(name, "wb");
    free(name);
    if (result.stream == NULL) merlo_file_error = UINT32_C(1);
    return result;
}
static MerloBytes merlo_file_read_chunk(MerloFileReader *reader, uint64_t limit) {
    merlo_require_capability(MERLO_EFFECT_FS_READ);
    merlo_file_error = 0;
    MerloBytes result = { NULL, 0 };
    if (reader == NULL || reader->stream == NULL) {
        merlo_file_error = UINT32_C(5);
        return result;
    }
    if (limit == 0) return result;
    result.data = (uint8_t *)malloc((size_t)limit);
    if (result.data == NULL) merlo_allocation_trap();
    size_t count = fread(result.data, 1, (size_t)limit, reader->stream);
    if (ferror(reader->stream)) {
        free(result.data);
        result.data = NULL;
        merlo_file_error = UINT32_C(2);
        return result;
    }
    result.length = (uint64_t)count;
    if (count != 0) ++merlo_allocations;
    return result;
}
static uint64_t merlo_file_write_chunk(MerloFileWriter *writer, const MerloBytesView *data) {
    merlo_require_capability(MERLO_EFFECT_FS_WRITE);
    merlo_file_error = 0;
    if (writer == NULL || writer->stream == NULL || data == NULL) {
        merlo_file_error = UINT32_C(5);
        return 1;
    }
    size_t written = data->length == 0
        ? 0
        : fwrite(data->data, 1, (size_t)data->length, writer->stream);
    if (written != (size_t)data->length) {
        merlo_file_error = UINT32_C(2);
        return 1;
    }
    return 0;
}
static MerloFileReader merlo_file_open_read(const MerloText *path) {
    merlo_require_capability(MERLO_EFFECT_FS_READ);
    merlo_file_error = 0;
    if (!merlo_path_allowed(path)) {
        merlo_file_error = UINT32_C(4);
        return (MerloFileReader){ NULL, NULL, 0, 0, 1, 0, 0, false };
    }
    MerloFileReader result = { NULL, NULL, 0, 0, 1, 0, 0, false };
    char *name = (char *)malloc((size_t)path->length + 1);
    if (name == NULL) merlo_allocation_trap();
    if (path->length != 0) memcpy(name, path->data, (size_t)path->length);
    name[path->length] = '\0';
    result.stream = fopen(name, "rb");
    if (result.stream == NULL) {
        merlo_file_error = UINT32_C(1);
    }
    free(name);
    return result;
}
static MerloBytes merlo_file_read_all(const MerloText *path) {
    merlo_require_capability(MERLO_EFFECT_FS_READ);
    merlo_file_error = 0;
    if (!merlo_path_allowed(path)) {
        merlo_file_error = UINT32_C(4);
        return (MerloBytes){ NULL, 0 };
    }
    MerloBytes result = { NULL, 0 };
    char *name = (char *)malloc((size_t)path->length + 1);
    if (name == NULL) merlo_allocation_trap();
    if (path->length != 0) memcpy(name, path->data, (size_t)path->length);
    name[path->length] = '\0';
    FILE *stream = fopen(name, "rb");
    free(name);
    if (stream == NULL) {
        merlo_file_error = UINT32_C(1);
        return result;
    }
    size_t capacity = 0;
    uint8_t chunk[4096];
    while (!feof(stream)) {
        size_t count = fread(chunk, 1, sizeof(chunk), stream);
        if (ferror(stream)) {
            free(result.data);
            result.data = NULL;
            result.length = 0;
            merlo_file_error = UINT32_C(2);
            fclose(stream);
            return result;
        }
        if (count == 0) break;
        if (result.length > UINT64_MAX - (uint64_t)count) {
            free(result.data);
            fclose(stream);
            merlo_overflow_trap("FileLength");
        }
        uint64_t required = result.length + (uint64_t)count;
        if (required > (uint64_t)capacity) {
            size_t next = capacity == 0 ? 4096 : capacity;
            while ((uint64_t)next < required) {
                if (next > SIZE_MAX / 2) {
                    next = (size_t)required;
                    break;
                }
                next *= 2;
            }
            uint8_t *grown = (uint8_t *)realloc(result.data, next);
            if (grown == NULL) {
                free(result.data);
                fclose(stream);
                merlo_allocation_trap();
            }
            result.data = grown;
            capacity = next;
        }
        memcpy(result.data + result.length, chunk, count);
        result.length = required;
    }
    if (fclose(stream) != 0) {
        free(result.data);
        result.data = NULL;
        result.length = 0;
        merlo_file_error = UINT32_C(2);
        return result;
    }
    if (result.data != NULL) ++merlo_allocations;
    return result;
}

static MerloFileLines merlo_file_lines(MerloFileReader *reader) {
    return (MerloFileLines){ reader, reader->generation };
}

static MerloText merlo_file_read_text(const MerloText *path) {
    MerloBytes bytes = merlo_file_read_all(path);
    if (merlo_file_error != 0 || bytes.data == NULL) {
        return (MerloText){ NULL, 0 };
    }
    if (!merlo_valid_utf8(bytes.data, bytes.length)) {
        free(bytes.data);
        ++merlo_frees;
        merlo_file_error = UINT32_C(3);
        return (MerloText){ NULL, 0 };
    }
    MerloBytesView view = merlo_bytes_as_view(&bytes);
    MerloText result = merlo_text_from_bytes(&view, 0, bytes.length);
    free(bytes.data);
    ++merlo_frees;
    return result;
}

static MerloTextView *merlo_file_next(MerloFileLines *lines) {
    static _Thread_local MerloTextView view;
    MerloFileReader *reader = lines->owner;
    if (reader == NULL || reader->stream == NULL || lines->generation != reader->generation) return NULL;
    char *line = (char *)reader->buffer;
    size_t capacity = (size_t)reader->buffer_capacity;
    ssize_t count = getline(&line, &capacity, reader->stream);
    reader->buffer = (uint8_t *)line;
    reader->buffer_capacity = (uint64_t)capacity;
    if (count < 0) {
        if (ferror(reader->stream)) {
            merlo_file_error = UINT32_C(2);
        }
        merlo_file_close(reader);
        return NULL;
    }
    reader->buffer_length = (uint64_t)count;
    if (reader->buffer_length != 0 && reader->buffer[reader->buffer_length - 1] == '\n') {
        --reader->buffer_length;
    }
    if (reader->buffer_length != 0 && reader->buffer[reader->buffer_length - 1] == '\r') {
        --reader->buffer_length;
    }
    ++reader->line_number;
    if (!merlo_valid_utf8(reader->buffer, reader->buffer_length)) {
        merlo_file_error = UINT32_C(3);
        merlo_file_error_line = reader->line_number;
        merlo_file_close(reader);
        return NULL;
    }
    ++reader->generation;
    lines->generation = reader->generation;
    view = (MerloTextView){ reader->buffer, reader->buffer_length };
    return &view;
}'''
    def _move_drop_glue(self) -> str:
        lines = []
        owners = [item for item in self.representation.descriptors if _is_owner(item)]
        for descriptor in owners:
            lines.append(f"static {_c_name(descriptor.name)} merlo_zero_{_identifier(descriptor.name)}(void);")
            lines.append(f"static {_c_name(descriptor.name)} merlo_move_{_identifier(descriptor.name)}({_c_name(descriptor.name)} *value);")
            lines.append(f"static void merlo_drop_{_identifier(descriptor.name)}({_c_name(descriptor.name)} *value);")
            lines.append(f"static {_c_name(descriptor.name)} merlo_clone_{_identifier(descriptor.name)}(const {_c_name(descriptor.name)} *value);")
        for descriptor in owners:
            lines.extend(self._emit_zero_move_drop(descriptor))
        return "\n".join(lines)

    def _closure_runtime(self) -> str:
        lines: list[str] = []
        for function in self.hir.functions:
            callback_type = "Fn[" + ",".join(
                (*[item.type_name for item in function.parameters], function.return_type)
            ) + "]"
            descriptor = self.descriptors.get(callback_type)
            if descriptor is None or descriptor.kind not in {"callback", "closure"}:
                continue
            if any(
                _is_owner(self.descriptors[item.type_name])
                for item in function.parameters
            ):
                continue
            parameters = ", ".join(
                f"{_c_name(item.type_name)} {item.name}"
                for item in function.parameters
            )
            signature = "void *environment" + (
                f", {parameters}" if parameters else ""
            )
            function_name = _identifier(function.name)
            lines.append(
                f"static {_c_name(function.return_type)} "
                f"merlo_closure_adapter_{function_name}({signature}) {{"
            )
            lines.append("    (void)environment;")
            call = f"merlo_fn_{function_name}(" + ", ".join(
                item.name for item in function.parameters
            ) + ")"
            if function.return_type == "Unit":
                lines.extend([f"    {call};", "}"])
            else:
                lines.extend([f"    return {call};", "}"])

        for node in self.closure_nodes:
            attrs = node.attribute_map
            closure_id = attrs.get("closure_id")
            parameters = attrs.get("parameters", ())
            return_type = attrs.get("return_type")
            captures = attrs.get("captures", ())
            body = attrs.get("closure_body")
            if (
                not isinstance(closure_id, str)
                or not isinstance(return_type, str)
                or not isinstance(parameters, (list, tuple))
                or not isinstance(captures, (list, tuple))
                or body is None
            ):
                raise RuntimeError("typed closure metadata is malformed")
            callback_type = "Fn[" + ",".join(
                (*[type_name for _name, type_name in parameters], return_type)
            ) + "]"
            ctype = _c_name(callback_type)
            environment_type = f"MerloClosureEnv_{closure_id}"
            lines.extend(
                [
                    f"static void merlo_closure_retain_{closure_id}(void *raw) {{",
                    f"    {environment_type} *environment = ({environment_type} *)raw;",
                    "    if (environment != NULL) ++environment->references;",
                    "}",
                    f"static void merlo_closure_release_{closure_id}(void *raw) {{",
                    f"    {environment_type} *environment = ({environment_type} *)raw;",
                    "    if (environment == NULL || --environment->references != 0) return;",
                ]
            )
            for name, type_name, ownership in captures:
                if ownership == "owned":
                    lines.append(
                        f"    merlo_drop_{_identifier(type_name)}"
                        f"(&environment->{name});"
                    )
            lines.extend(
                [
                    "    free(environment);",
                    "    ++merlo_frees;",
                    "}",
                ]
            )

            parameter_declarations = ", ".join(
                f"{_c_name(type_name)} {name}"
                for name, type_name in parameters
            )
            call_signature = "void *raw" + (
                f", {parameter_declarations}" if parameter_declarations else ""
            )
            lines.extend(
                [
                    f"static {_c_name(return_type)} "
                    f"merlo_closure_call_{closure_id}({call_signature}) {{",
                    f"    {environment_type} *environment = "
                    f"({environment_type} *)raw;",
                ]
            )
            owned_captures = {
                name
                for name, _type_name, ownership in captures
                if ownership == "owned"
            }
            local_types = {
                name: (
                    f"Borrow[{type_name}]"
                    if ownership == "owned"
                    else type_name
                )
                for name, type_name, ownership in captures
            }
            local_types.update(
                {name: type_name for name, type_name in parameters}
            )
            if not captures:
                lines.append("    (void)environment;")
            for name, type_name, ownership in captures:
                if ownership == "owned":
                    lines.append(
                        f"    const {_c_name(type_name)} *{name} = "
                        f"&environment->{name};"
                    )
                else:
                    lines.append(
                        f"    {_c_name(type_name)} {name} = environment->{name};"
                    )
            expression = self._closure_expression(body, local_types)
            if (
                getattr(body, "kind", "") == "Name"
                and body.attribute_map.get("name") in owned_captures
            ):
                expression = (
                    f"merlo_clone_{_identifier(return_type)}"
                    f"({body.attribute_map['name']})"
                )
            if return_type == "Unit":
                lines.extend([f"    (void)({expression});", "    return;"])
            else:
                lines.extend(
                    [
                        f"    {_c_name(return_type)} result = {expression};",
                        "    return result;",
                    ]
                )
            lines.append("}")

            make_parameters = []
            for name, type_name, ownership in captures:
                pointer = "const " if ownership == "owned" else ""
                suffix = " *" if ownership == "owned" else " "
                make_parameters.append(
                    f"{pointer}{_c_name(type_name)}{suffix}{name}"
                )
            lines.append(
                f"static {ctype} merlo_closure_make_{closure_id}"
                f"({', '.join(make_parameters) if make_parameters else 'void'}) {{"
            )
            if not captures:
                lines.extend(
                    [
                        f"    return ({ctype}){{ "
                        f"merlo_closure_call_{closure_id}, NULL, NULL, NULL }};",
                        "}",
                    ]
                )
                continue
            lines.extend(
                [
                    f"    {environment_type} *environment = "
                    f"({environment_type} *)malloc(sizeof({environment_type}));",
                    "    if (environment == NULL) merlo_allocation_trap();",
                    "    environment->references = UINT64_C(1);",
                ]
            )
            for name, type_name, ownership in captures:
                value = (
                    f"merlo_clone_{_identifier(type_name)}({name})"
                    if ownership == "owned"
                    else name
                )
                lines.append(f"    environment->{name} = {value};")
            lines.extend(
                [
                    "    ++merlo_allocations;",
                    f"    return ({ctype}){{ "
                    f"merlo_closure_call_{closure_id}, environment, "
                    f"merlo_closure_retain_{closure_id}, "
                    f"merlo_closure_release_{closure_id} }};",
                    "}",
                ]
            )
        return "\n".join(lines)

    def _emit_zero_move_drop(self, descriptor: TypeDescriptor) -> list[str]:
        ctype = _c_name(descriptor.name)
        suffix = _identifier(descriptor.name)
        lines = [
            f"static {ctype} merlo_zero_{suffix}(void) {{",
            f"    {ctype} result;",
            "    memset(&result, 0, sizeof(result));",
        ]
        if descriptor.kind == "enum":
            lines.append(f"    result.tag = MERLO_{suffix}_MOVED_TAG;")
        elif descriptor.kind == "record":
            for field_name, field_type, _ in descriptor.fields:
                field_descriptor = self.descriptors[field_type]
                if _is_owner(field_descriptor):
                    lines.append(f"    result.{field_name} = merlo_zero_{_identifier(field_type)}();")
        lines.extend(["    return result;", "}"])
        lines.extend(
            [
                f"static {ctype} merlo_move_{suffix}({ctype} *value) {{",
                f"    {ctype} result = *value;",
                f"    *value = merlo_zero_{suffix}();",
                "    return result;",
                "}",
                f"static void merlo_drop_{suffix}({ctype} *value) {{",
            ]
        )
        if descriptor.kind == "closure":
            lines.extend([
                "    if (value->environment == NULL) return;",
                "    if (value->release != NULL) value->release(value->environment);",
                "    value->call = NULL; value->environment = NULL;",
                "    value->retain = NULL; value->release = NULL;",
                "    ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "text":
            lines.extend([
                "    if (value->data == NULL) return;",
                "    free(value->data);",
                "    value->data = NULL; value->length = 0;",
                "    ++merlo_frees; ++merlo_text_frees; ++merlo_drop_calls;",
            ])
        elif descriptor.name == "TextBuilder":
            lines.extend([
                "    if (value->data == NULL) return;",
                "    free(value->data);",
                "    value->data = NULL; value->length = 0; value->capacity = 0;",
                "    ++merlo_frees; ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "bytes":
            lines.extend([
                "    if (value->data == NULL) return;",
                "    free(value->data); value->data = NULL; value->length = 0;",
                "    ++merlo_frees; ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "vec":
            assert descriptor.element_type is not None
            element = self.descriptors[descriptor.element_type]
            lines.append("    if (value->active_views != 0) merlo_ownership_trap(\"VecDropDuringView\");")
            if _is_owner(element):
                lines.extend([
                    "    for (uint64_t index = 0; index < value->length; ++index) {",
                    f"        merlo_drop_{_identifier(descriptor.element_type)}(&value->data[index]);",
                    "        ++merlo_vec_elements_dropped;",
                    "    }",
                ])
            else:
                lines.append("    merlo_vec_elements_dropped += value->length;")
            lines.extend([
                "    if (value->data != NULL) { free(value->data); ++merlo_frees; ++merlo_vec_frees; }",
                "    value->data = NULL; value->length = 0; value->capacity = 0; value->active_views = 0;",
                "    ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "box":
            assert descriptor.payload_type is not None
            lines.extend([
                "    if (value->data == NULL) return;",
            ])
            if _is_owner(self.descriptors[descriptor.payload_type]):
                lines.append(f"    merlo_drop_{_identifier(descriptor.payload_type)}(value->data);")
            lines.extend([
                "    free(value->data); value->data = NULL;",
                "    ++merlo_frees; ++merlo_box_frees; ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "map":
            assert descriptor.key_type is not None
            assert descriptor.value_type is not None
            lines.extend([
                "    if (value->active_views != 0) merlo_ownership_trap(\"MapDropDuringView\");",
                "    for (uint64_t index = 0; index < value->length; ++index) {",
                "        if (value->entries[index].key.data != NULL) ++merlo_map_frees;",
                f"        merlo_drop_{_identifier(descriptor.key_type)}(&value->entries[index].key);",
            ])
            if _is_owner(self.descriptors[descriptor.value_type]):
                lines.append(
                    f"        merlo_drop_{_identifier(descriptor.value_type)}"
                    "(&value->entries[index].value);"
                )
            lines.extend([
                "        ++merlo_map_owned_keys_dropped;",
                "    }",
                "    if (value->entries != NULL) {",
                "        free(value->entries); ++merlo_frees; ++merlo_map_frees;",
                "    }",
                "    if (value->buckets != NULL) {",
                "        free(value->buckets); ++merlo_frees; ++merlo_map_frees;",
                "    }",
                "    value->entries = NULL; value->buckets = NULL;",
                "    value->length = 0; value->capacity = 0; value->active_views = 0;",
                "    ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "file_reader":
            lines.extend([
                "    merlo_file_close(value);",
                "    ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "file_writer":
            lines.extend([
                "    merlo_file_close_writer(value);",
                "    ++merlo_drop_calls;",
            ])
        elif descriptor.kind == "array":
            assert descriptor.element_type is not None
            assert descriptor.length is not None
            if _is_owner(self.descriptors[descriptor.element_type]):
                lines.extend(
                    [
                        f"    for (uint64_t index = 0; index < UINT64_C({descriptor.length}); ++index) {{",
                        f"        merlo_drop_{_identifier(descriptor.element_type)}(&value->data[index]);",
                        "    }",
                    ]
                )
            lines.append("    ++merlo_drop_calls;")
        elif descriptor.kind == "record":
            for field_name, field_type, _ in descriptor.fields:
                if _is_owner(self.descriptors[field_type]):
                    lines.append(f"    merlo_drop_{_identifier(field_type)}(&value->{field_name});")
            lines.append("    ++merlo_drop_calls;")
        elif descriptor.kind == "enum":
            lines.append(f"    if (value->tag == MERLO_{suffix}_MOVED_TAG) return;")
            lines.append("    switch (value->tag) {")
            for variant, payload, tag in descriptor.variants:
                lines.append(f"    case UINT32_C({tag}):")
                if payload is not None and _is_owner(self.descriptors[payload]):
                    lines.append(f"        merlo_drop_{_identifier(payload)}(&value->payload.{variant});")
                lines.append("        break;")
            lines.extend([
                "    default: merlo_ownership_trap(\"InvalidEnumTagDuringDrop\");",
                "    }",
                f"    value->tag = MERLO_{suffix}_MOVED_TAG;",
                "    ++merlo_ast_nodes_freed; ++merlo_drop_calls;",
            ])
        lines.append("}")
        if descriptor.kind == "closure":
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                f"    {ctype} result = *value;",
                "    if (result.environment != NULL && result.retain != NULL) {",
                "        result.retain(result.environment);",
                "    }",
                "    return result;",
                "}",
            ])
        elif descriptor.kind == "text":
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                "    return merlo_text_clone(value);",
                "}",
            ])
        elif descriptor.kind == "record":
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                f"    {ctype} result = *value;",
            ])
            for field_name, field_type, _ in descriptor.fields:
                if _is_owner(self.descriptors[field_type]):
                    lines.append(
                        f"    result.{field_name} = merlo_clone_{_identifier(field_type)}(&value->{field_name});"
                    )
            lines.extend(["    return result;", "}"])
        elif descriptor.kind == "enum":
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                f"    {ctype} result = *value;",
                "    switch (value->tag) {",
            ])
            for variant, payload, tag in descriptor.variants:
                lines.append(f"    case UINT32_C({tag}):")
                if payload is not None and payload != "Unit" and _is_owner(self.descriptors[payload]):
                    lines.append(
                        f"        result.payload.{variant} = merlo_clone_{_identifier(payload)}(&value->payload.{variant});"
                    )
                lines.append("        break;")
            lines.extend(["    default: merlo_ownership_trap(\"InvalidEnumTagDuringClone\");", "    }", "    return result;", "}"])
        elif descriptor.kind == "vec":
            assert descriptor.element_type is not None
            element = self.descriptors[descriptor.element_type]
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                f"    {ctype} result = merlo_zero_{suffix}();",
                "    result.length = value->length; result.capacity = value->length;",
                "    if (value->length != 0) {",
                f"        result.data = ({_c_name(descriptor.element_type)} *)malloc((size_t)value->length * sizeof({_c_name(descriptor.element_type)}));",
                "        if (result.data == NULL) merlo_allocation_trap();",
                "        ++merlo_allocations;",
                "        for (uint64_t index = 0; index < value->length; ++index) {",
            ])
            if _is_owner(element):
                lines.append(
                    f"            result.data[index] = merlo_clone_{_identifier(descriptor.element_type)}(&value->data[index]);"
                )
            else:
                lines.append("            result.data[index] = value->data[index];")
            lines.extend(["        }", "    }", "    return result;", "}"])
        elif descriptor.kind == "box":
            assert descriptor.payload_type is not None
            payload = self.descriptors[descriptor.payload_type]
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                f"    {ctype} result = merlo_zero_{suffix}();",
                f"    if (value->data != NULL) {{ result.data = ({_c_name(descriptor.payload_type)} *)malloc(sizeof({_c_name(descriptor.payload_type)})); if (result.data == NULL) merlo_allocation_trap(); ++merlo_allocations;",
            ])
            if _is_owner(payload):
                lines.append(
                    f"        *result.data = merlo_clone_{_identifier(descriptor.payload_type)}(value->data);"
                )
            else:
                lines.append("        *result.data = *value->data;")
            lines.extend(["    }", "    return result;", "}"])
        else:
            lines.extend([
                f"static {ctype} merlo_clone_{suffix}(const {ctype} *value) {{",
                "    return *value;",
                "}",
            ])
        return lines
