cmake_minimum_required(VERSION 3.16)

project(x48-sdl
    VERSION 0.9.0
    DESCRIPTION "HP 48 emulator: libcalc48 core + SDL front end"
    LANGUAGES C)

# The codebase is plain C (no C++); requires a modern C compiler.
set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# Collect all build artifacts in the build root, so the executable and the
# shared library are simply build/x48 and build/libcalc48.so regardless of which
# subproject produced them (keeps examples/hp48.py and ./build/x48 working).
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})

# Platform define, shared with the subprojects (mirrors the old -DPLATFORM_*).
if(WIN32)
    set(PLATFORM_DEFINE PLATFORM_WIN32)
elseif(APPLE)
    set(PLATFORM_DEFINE PLATFORM_DARWIN)
else()
    set(PLATFORM_DEFINE PLATFORM_LINUX)
endif()

# Subproject 1: the emulator core (no SDL, no UI).
add_subdirectory(libcalc48)

# Subproject 2: the SDL front end (links the core).
add_subdirectory(sdl)

# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------
# Headless embedding example: links ONLY the core, so it keeps us honest that
# libcalc48 is self-contained and usable without a UI toolkit.
add_executable(hp48_ffi_smoke examples/ffi_smoke.c)
target_link_libraries(hp48_ffi_smoke PRIVATE calc48)
