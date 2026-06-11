cmake_minimum_required(VERSION 3.16)

project(x48-sdl
    VERSION 0.9.0
    DESCRIPTION "SDL port of the x48 HP 48 emulator"
    LANGUAGES C)

# ---------------------------------------------------------------------------
# Build options
# ---------------------------------------------------------------------------
# The codebase historically compiled with g++, but it is plain C and only
# relied on C++ for incidental reasons (inline linkage, transitive libm).
# We build it as C now so the eventual libhp48 core has no C++ dependency.

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# ---------------------------------------------------------------------------
# Source layout
# ---------------------------------------------------------------------------
# These two lists are deliberately kept separate. Today everything is linked
# into one executable, but in Phase 1 of REFACTOR_PLAN.md the CORE list
# becomes a standalone `libhp48` target with no SDL dependency, and the
# FRONTEND list becomes the SDL client that links against it.

set(CORE_SOURCES
    src/actions.c
    src/debugger.c
    src/device.c
    src/disasm.c
    src/emulate.c
    src/errors.c
    src/init.c
    src/lcd.c
    src/memory.c
    src/register.c
    src/resources.c
    src/romio.c
    src/rpl.c
    src/serial.c
    src/timer.c
)

set(FRONTEND_SOURCES
    src/mainsdl.c
    src/x48_sdl.c
    src/SDL_gfxPrimitives.c
    src/SDL_rotozoom.c
)

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
find_package(SDL REQUIRED)   # SDL 1.2 (FindSDL provides SDL::SDL)

# libm: the rotozoom/gfx helpers use sincos/ceil. g++ pulled this in
# transitively; in C we must request it explicitly.
find_library(MATH_LIBRARY m)

# ---------------------------------------------------------------------------
# Executable (single target for now; split in Phase 1)
# ---------------------------------------------------------------------------
add_executable(x48 ${CORE_SOURCES} ${FRONTEND_SOURCES})

target_include_directories(x48 PRIVATE src)

target_compile_definitions(x48 PRIVATE WITH_SDL)

# Platform define mirrors the historical Makefile (-DPLATFORM_LINUX).
if(WIN32)
    target_compile_definitions(x48 PRIVATE PLATFORM_WIN32)
elseif(APPLE)
    target_compile_definitions(x48 PRIVATE PLATFORM_DARWIN)
else()
    target_compile_definitions(x48 PRIVATE PLATFORM_LINUX)
endif()

# serial.c uses grantpt/unlockpt/ptsname_r, which glibc gates behind
# _GNU_SOURCE. Harmless elsewhere.
if(UNIX AND NOT APPLE)
    target_compile_definitions(x48 PRIVATE _GNU_SOURCE)
endif()

# FindSDL exposes either the SDL::SDL imported target (newer CMake) or the
# classic variables; support both.
if(TARGET SDL::SDL)
    target_link_libraries(x48 PRIVATE SDL::SDL)
else()
    target_include_directories(x48 PRIVATE ${SDL_INCLUDE_DIRS})
    target_link_libraries(x48 PRIVATE ${SDL_LIBRARIES})
endif()

if(MATH_LIBRARY)
    target_link_libraries(x48 PRIVATE ${MATH_LIBRARY})
endif()

# Output is named "x48"; CMake appends the platform-appropriate executable
# suffix automatically (none on Linux/macOS, ".exe" on Windows). The old
# ad-hoc Cygwin Makefile's "main.exe" name is intentionally dropped.
