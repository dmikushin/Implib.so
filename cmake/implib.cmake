find_package(Python3 REQUIRED COMPONENTS Interpreter)

enable_language(ASM)

function(target_link_implib_libraries target)
  set(options PUBLIC PRIVATE INTERFACE)
  set(oneValueArgs DLOPEN_CALLBACK)
  set(multiValueArgs)
  cmake_parse_arguments(ARG "${options}" "${oneValueArgs}" "${multiValueArgs}" ${ARGN})

  if (ARG_PUBLIC)
    set(linkage PUBLIC)
  elseif (ARG_PRIVATE)
    set(linkage PRIVATE)
  elseif (ARG_INTERFACE)
    set(linkage INTERFACE)
  else()
    set(linkage "")
  endif()

  set(dlopen_callback_arg "")
  if (ARG_DLOPEN_CALLBACK)
    set(dlopen_callback_arg "--dlopen-callback=${ARG_DLOPEN_CALLBACK}")
  endif()

  foreach(implib IN LISTS ARG_UNPARSED_ARGUMENTS)
    if (NOT TARGET ${implib})
      message(FATAL_ERROR "${implib} must be a CMake target")
    endif()
    set(tramp_file ${CMAKE_CURRENT_BINARY_DIR}/${implib}.tramp.S)
    set(init_file ${CMAKE_CURRENT_BINARY_DIR}/${implib}.init.c)
    set(implib-gen ${CMAKE_CURRENT_FUNCTION_LIST_DIR}/../implib-gen.py)

    add_custom_command(
      OUTPUT ${tramp_file} ${init_file}
      COMMAND ${implib-gen} -q
        --target ${CMAKE_SYSTEM_PROCESSOR}
        --vtables
        ${dlopen_callback_arg}
        #--undefined-symbols
        --suffix ${implib} $<TARGET_FILE:${implib}>
      DEPENDS ${implib} ${implib-gen}
    )

    set_source_files_properties(${tramp_file} PROPERTIES GENERATED TRUE)
    set_source_files_properties(${init_file} PROPERTIES GENERATED TRUE)

    target_sources(${target} PRIVATE ${tramp_file} ${init_file})

    # Copy public include directories
    get_target_property(PUBLIC_INCLUDES ${implib} INCLUDE_DIRECTORIES)
    if (PUBLIC_INCLUDES)
      target_include_directories(${target} ${linkage} ${PUBLIC_INCLUDES})
    endif()
  endforeach()

  # Link -ldl only once
  get_target_property(LINKED_LIBRARIES ${target} LINK_LIBRARIES)
  if (LINKED_LIBRARIES)
    if (NOT (${CMAKE_DL_LIBS} IN_LIST LINKED_LIBRARIES))
      target_link_libraries(${target} ${linkage} ${CMAKE_DL_LIBS})
    endif()
  else()
    target_link_libraries(${target} ${linkage} ${CMAKE_DL_LIBS})
  endif()
endfunction()

