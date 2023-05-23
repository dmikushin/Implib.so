cmake_minimum_required(VERSION 3.10)

project(implib)

include(cmake/implib.cmake)

enable_testing()

add_subdirectory(tests/basic)
