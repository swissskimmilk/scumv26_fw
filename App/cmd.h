/* Text command interpreter.
 *
 * Protocol: the host sends one command per line. The reply is any number of text
 * lines (or, for `cap`, a binary block announced by its own header) followed by a
 * final "OK" or "ERR <reason>" line, so a host script always knows when it is done.
 */
#ifndef CMD_H
#define CMD_H

#include <stdbool.h>
#include <stdint.h>

/* Handlers return true for OK; on failure they call cmd_err() and return its value. */
typedef bool (*cmd_fn)(int argc, char **argv);

void cmd_execute(char *line);

bool cmd_err(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/* Argument helpers for handlers. */
bool arg_u32(const char *s, uint32_t *out);  /* decimal or 0x hex */
bool arg_float(const char *s, float *out);
bool arg_onoff(const char *s, bool *out);    /* on/off/1/0 */

#endif
