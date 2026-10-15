/* USB descriptors: one CDC-ACM interface ("SCuMV26 bring-up"). */
#include <string.h>

#include "main.h"
#include "tusb.h"

/* pid.codes test PID; fine for a lab tool, replace before any distribution. */
#define USB_VID 0x1209
#define USB_PID 0x0001
#define USB_BCD 0x0200

static tusb_desc_device_t const desc_device = {
    .bLength = sizeof(tusb_desc_device_t),
    .bDescriptorType = TUSB_DESC_DEVICE,
    .bcdUSB = USB_BCD,
    .bDeviceClass = TUSB_CLASS_MISC,
    .bDeviceSubClass = MISC_SUBCLASS_COMMON,
    .bDeviceProtocol = MISC_PROTOCOL_IAD,
    .bMaxPacketSize0 = CFG_TUD_ENDPOINT0_SIZE,
    .idVendor = USB_VID,
    .idProduct = USB_PID,
    .bcdDevice = 0x0100,
    .iManufacturer = 0x01,
    .iProduct = 0x02,
    .iSerialNumber = 0x03,
    .bNumConfigurations = 0x01,
};

uint8_t const *tud_descriptor_device_cb(void)
{
  return (uint8_t const *)&desc_device;
}

enum { ITF_NUM_CDC = 0, ITF_NUM_CDC_DATA, ITF_NUM_TOTAL };

#define EPNUM_CDC_NOTIF 0x81
#define EPNUM_CDC_OUT   0x02
#define EPNUM_CDC_IN    0x82
#define CONFIG_TOTAL_LEN (TUD_CONFIG_DESC_LEN + TUD_CDC_DESC_LEN)

static uint8_t const desc_configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, ITF_NUM_TOTAL, 0, CONFIG_TOTAL_LEN, 0x00, 100),
    TUD_CDC_DESCRIPTOR(ITF_NUM_CDC, 4, EPNUM_CDC_NOTIF, 8, EPNUM_CDC_OUT, EPNUM_CDC_IN, 64),
};

uint8_t const *tud_descriptor_configuration_cb(uint8_t index)
{
  (void)index;
  return desc_configuration;
}

static char const *const string_desc[] = {
    NULL, /* 0: language, handled below */
    "UC Berkeley",
    "SCuMV26 bring-up",
    NULL, /* 3: serial, from the MCU UID */
    "SCuMV26 CDC",
};

static uint16_t desc_str[33];

uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid)
{
  (void)langid;
  uint8_t chr_count;

  if (index == 0) {
    desc_str[1] = 0x0409;
    chr_count = 1;
  } else if (index == 3) {
    /* 96-bit unique ID -> 24 hex digits */
    static const char hex[] = "0123456789ABCDEF";
    const uint32_t uid[3] = {HAL_GetUIDw0(), HAL_GetUIDw1(), HAL_GetUIDw2()};
    chr_count = 0;
    for (int w = 0; w < 3; w++) {
      for (int nib = 7; nib >= 0; nib--) {
        desc_str[1 + chr_count++] = (uint16_t)hex[(uid[w] >> (nib * 4)) & 0xFU];
      }
    }
  } else {
    if (index >= sizeof(string_desc) / sizeof(string_desc[0]) || string_desc[index] == NULL) {
      return NULL;
    }
    const char *s = string_desc[index];
    chr_count = (uint8_t)strlen(s);
    if (chr_count > 32) {
      chr_count = 32;
    }
    for (uint8_t i = 0; i < chr_count; i++) {
      desc_str[1 + i] = (uint16_t)s[i];
    }
  }
  desc_str[0] = (uint16_t)((TUSB_DESC_STRING << 8) | (2 * chr_count + 2));
  return desc_str;
}
