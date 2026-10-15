cmake_minimum_required(VERSION 3.22)

#
# This file is generated only once,
# and is not re-generated if converter is called multiple times.
#
# User is free to modify the file as much as necessary
#

# Setup compiler settings
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)


# Define the build type
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Debug")
endif()

# Set the project name
set(CMAKE_PROJECT_NAME scumv26_fw)

# Enable compile command to ease indexing with e.g. clangd
set(CMAKE_EXPORT_COMPILE_COMMANDS TRUE)

# Core project settings
project(${CMAKE_PROJECT_NAME})
message("Build type: " ${CMAKE_BUILD_TYPE})

# Enable CMake support for ASM and C languages
enable_language(C ASM)

# Create an executable object type
add_executable(${CMAKE_PROJECT_NAME})

# Add STM32CubeMX generated sources
add_subdirectory(cmake/stm32cubemx)

# Link directories setup
target_link_directories(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined library search paths
)

# Application code (everything outside Core/ and Drivers/ is ours; CubeMX never touches it)
file(GLOB APP_SOURCES CONFIGURE_DEPENDS ${CMAKE_SOURCE_DIR}/App/*.c ${CMAKE_SOURCE_DIR}/App/usb/*.c)

# TinyUSB device stack: CDC only, on the OTG_FS (DWC2) core
set(TUSB ${CMAKE_SOURCE_DIR}/lib/tinyusb/src)
set(TUSB_SOURCES
    ${TUSB}/tusb.c
    ${TUSB}/common/tusb_fifo.c
    ${TUSB}/device/usbd.c
    ${TUSB}/device/usbd_control.c
    ${TUSB}/class/cdc/cdc_device.c
    ${TUSB}/portable/synopsys/dwc2/dcd_dwc2.c
    ${TUSB}/portable/synopsys/dwc2/dwc2_common.c
)

# Add sources to executable
target_sources(${CMAKE_PROJECT_NAME} PRIVATE
    ${APP_SOURCES}
    ${TUSB_SOURCES}
)

# Add include paths
target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    ${CMAKE_SOURCE_DIR}/App
    ${CMAKE_SOURCE_DIR}/App/usb
    ${TUSB}
)

# Add project symbols (macros)
target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined symbols (TinyUSB's live in App/usb/tusb_config.h)
)

# Our code builds warning-clean; vendor code keeps its own flags.
set_source_files_properties(${APP_SOURCES} PROPERTIES COMPILE_OPTIONS "-Wall;-Wextra")

# printf("%f") for telemetry readouts (newlib-nano leaves float formatting out by default)
target_link_options(${CMAKE_PROJECT_NAME} PRIVATE -u _printf_float)

# Remove wrong libob.a library dependency when using cpp files
list(REMOVE_ITEM CMAKE_C_IMPLICIT_LINK_LIBRARIES ob)

# Add linked libraries
target_link_libraries(${CMAKE_PROJECT_NAME}
    stm32cubemx

    # Add user defined libraries
)
