# ---------------------------------------------------------------------------
# libcalc48 -- the HP 48 emulator core
# ---------------------------------------------------------------------------
# A self-contained, instantiable, plain-C emulator with NO SDL and NO UI
# dependency.  It talks to a front end through callbacks (hp48_ui.h) and to
# storage through callbacks (hp48_io.h); it performs no filesystem access of
# its own (the bundled stdio provider below is optional).  Built both ways:
#   - STATIC  calc48        (libcalc48.a)  -> linked by the SDL app and examples
#   - SHARED  calc48_shared (libcalc48.so) -> for FFI consumers (Python, Qt, ...)
# (The C API keeps the hp48_* names: it emulates the HP 48 device.)

set(CORE_SOURCES
    src/context.c
    src/actions.c
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

# The core does no filesystem access; the bundled stdio storage provider
# (hp48_io_stdio.c) is the only part that calls fopen/mkdir, and it is opt-out
# so a restricted target (Android, iOS, console, WASM) can ship a libcalc48
# with no filesystem references and install its own hp48_io_t via hp48_set_io().
option(HP48_WITH_STDIO_IO "Build the bundled stdio file storage provider" ON)
if(HP48_WITH_STDIO_IO)
    list(APPEND CORE_SOURCES src/hp48_io_stdio.c)
endif()

# The UART emulation (serial.c) is portable and always built; the bundled serial
# *transport* providers (PTY/device + socket) are the only part that touches host
# syscalls (open/termios/socket), so they are opt-out too.  With this OFF the
# core has no serial host syscalls and the host installs its own hp48_serial_t.
option(HP48_WITH_SERIAL "Build the bundled PTY and socket serial providers" ON)
if(HP48_WITH_SERIAL)
    list(APPEND CORE_SOURCES src/hp48_serial_pty.c src/hp48_serial_socket.c)
endif()

# Runtime RPL user-stack read/write API (hp48_rpl.h / rplstack.c).  Supports both
# SX and GX, selected at runtime from the loaded ROM (opt_gx); HP49 is not
# supported.  On by default; a minimal/embedded build can turn it off.
# See docs/stack_io_plan.md.
option(HP48_WITH_STACK_IO "Build the RPL stack read/write API" ON)
if(HP48_WITH_STACK_IO)
    list(APPEND CORE_SOURCES src/rplstack.c)
endif()

# The interactive debugger (debugger.c) reads commands from stdin and is a
# terminal-bound development tool.  On by default; turn it off for an embedded /
# headless build and an inert stub (debugger_stub.c) is compiled instead.
option(HP48_WITH_DEBUGGER "Build the interactive stdin debugger" ON)
if(HP48_WITH_DEBUGGER)
    list(APPEND CORE_SOURCES src/debugger.c)
else()
    list(APPEND CORE_SOURCES src/debugger_stub.c)
endif()

function(configure_calc48_core tgt)
    # Public headers (hp48.h, hp48_ui.h, hp48_io.h, ...) are in include/ and
    # propagate to anything that links this target.
    target_include_directories(${tgt} PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
    target_compile_definitions(${tgt} PUBLIC ${PLATFORM_DEFINE})
    # serial.c uses grantpt/unlockpt/ptsname_r, gated behind _GNU_SOURCE on glibc.
    if(UNIX AND NOT APPLE)
        target_compile_definitions(${tgt} PRIVATE _GNU_SOURCE)
    endif()
    set_target_properties(${tgt} PROPERTIES POSITION_INDEPENDENT_CODE ON)
endfunction()

add_library(calc48 STATIC ${CORE_SOURCES})
configure_calc48_core(calc48)

add_library(calc48_shared SHARED ${CORE_SOURCES})
configure_calc48_core(calc48_shared)
set_target_properties(calc48_shared PROPERTIES
    OUTPUT_NAME calc48
    VERSION ${PROJECT_VERSION}
    SOVERSION ${PROJECT_VERSION_MAJOR})
