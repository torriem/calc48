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
# We build it as C now so the libhp48 core has no C++ dependency.

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# ---------------------------------------------------------------------------
# Source layout
# ---------------------------------------------------------------------------
# CORE  -> libhp48: the emulator. The end goal (Phase 3 of REFACTOR_PLAN.md)
#          is for this to have NO SDL dependency at all. Today three core
#          files (lcd.c, emulate.c, actions.c) still call into the SDL
#          frontend (SDLDrawNibble/SDLDrawAnnunc/SDLGetEvent) and include
#          x48_sdl.h, so the library is built STATIC and those symbols are
#          resolved when the frontend executable links. The SDL include path
#          and WITH_SDL define on the core below are TEMPORARY and removed in
#          Phase 3 once those calls move behind a callback interface.
# FRONTEND -> x48: the SDL client that links libhp48.

set(CORE_SOURCES
    src/context.c
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

# Normalize SDL into a single INTERFACE target regardless of CMake version
# (newer FindSDL provides SDL::SDL; older only sets the classic variables).
add_library(sdl_compat INTERFACE)
if(TARGET SDL::SDL)
    target_link_libraries(sdl_compat INTERFACE SDL::SDL)
else()
    target_include_directories(sdl_compat INTERFACE ${SDL_INCLUDE_DIRS})
    target_link_libraries(sdl_compat INTERFACE ${SDL_LIBRARIES})
endif()

# Platform define mirroring the historical Makefile (-DPLATFORM_LINUX), shared
# by both targets.
if(WIN32)
    set(PLATFORM_DEFINE PLATFORM_WIN32)
elseif(APPLE)
    set(PLATFORM_DEFINE PLATFORM_DARWIN)
else()
    set(PLATFORM_DEFINE PLATFORM_LINUX)
endif()

# ---------------------------------------------------------------------------
# libhp48: emulator core
# ---------------------------------------------------------------------------
add_library(hp48 STATIC ${CORE_SOURCES})

target_include_directories(hp48 PUBLIC src)
target_compile_definitions(hp48 PUBLIC ${PLATFORM_DEFINE})

# serial.c uses grantpt/unlockpt/ptsname_r, gated behind _GNU_SOURCE on glibc.
if(UNIX AND NOT APPLE)
    target_compile_definitions(hp48 PRIVATE _GNU_SOURCE)
endif()

# As of Phase 3 the core has NO SDL dependency: it talks to any front end
# through the hp48_ui_t callbacks (hp48_ui.h) and references no SDL symbols.
# It therefore links with neither SDL nor libm, and can be built shared.

# ---------------------------------------------------------------------------
# x48: SDL frontend executable
# ---------------------------------------------------------------------------
add_executable(x48 ${FRONTEND_SOURCES})

target_include_directories(x48 PRIVATE src)
target_compile_definitions(x48 PRIVATE WITH_SDL)

target_link_libraries(x48 PRIVATE hp48 sdl_compat)
if(MATH_LIBRARY)
    target_link_libraries(x48 PRIVATE ${MATH_LIBRARY})
endif()

# Output is named "x48"; CMake appends the platform-appropriate executable
# suffix automatically (none on Linux/macOS, ".exe" on Windows).

# ---------------------------------------------------------------------------
# hp48_ffi_smoke: headless embedding example / regression
# ---------------------------------------------------------------------------
# Links ONLY the core library (no SDL), so building it every time keeps us
# honest that libhp48 is self-contained and usable without a UI toolkit.
add_executable(hp48_ffi_smoke examples/ffi_smoke.c)
target_link_libraries(hp48_ffi_smoke PRIVATE hp48)
