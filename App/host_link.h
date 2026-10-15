/* USB CDC link to the PC: line-based commands in, text and binary out. */
#ifndef HOST_LINK_H
#define HOST_LINK_H

#include <stdbool.h>
#include <stdint.h>

void host_link_init(void);
void host_link_poll(void);
bool host_connected(void);
/* Keep USB alive from inside a long-running command (no command parsing). */
void host_link_service(void);

/* printf to the host. Blocks (servicing USB) while the TX FIFO is full. */
void out(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
/* Raw bytes to the host, same blocking behaviour. */
void out_write(const void *data, uint32_t len);

#endif
