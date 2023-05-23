# Set CFLAGS
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -g -O2 -Wno-unused-command-line-argument")

# Add shared library
add_library(interposed SHARED interposed.c)

# Add executable for standalone test
add_executable(standalone main.c test.c)
target_link_implib_libraries(standalone interposed)

# Add shared library for shlib test
add_library(shlib SHARED shlib.c test.c)
target_link_implib_libraries(shlib interposed)

# Add executable for shlib test
add_executable(shlib_test main.c)
target_link_libraries(shlib_test shlib)

# Set runtime path for executables
set_target_properties(standalone shlib_test PROPERTIES
  BUILD_RPATH "$ORIGIN"
  INSTALL_RPATH "$ORIGIN"
)

set(RUN_TEST ${CMAKE_CURRENT_SOURCE_DIR}/../run_test.sh)

add_test(NAME StandaloneTest
    COMMAND ${CMAKE_COMMAND} -E env LD_LIBRARY_PATH=$<TARGET_FILE_DIR:interposed> ${RUN_TEST} $<TARGET_FILE:standalone> standalone.out.log ${CMAKE_CURRENT_SOURCE_DIR}/test.ref
)

add_test(NAME ShlibTest
    COMMAND ${CMAKE_COMMAND} -E env LD_LIBRARY_PATH=$<TARGET_FILE_DIR:shlib> ${RUN_TEST} $<TARGET_FILE:shlib_test> shlib.out.log ${CMAKE_CURRENT_SOURCE_DIR}/test.ref
)

