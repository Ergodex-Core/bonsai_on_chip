#pragma once

// Require both a rule copts define and a transitive CcInfo define while the
// generated PCH is parsed. Their absence makes creation fail immediately.
#ifndef PCH_FIXTURE_BIAS
#error "PCH did not inherit the generated-C++ user flags"
#endif
#ifndef PCH_DEPENDENCY_BIAS
#error "PCH did not inherit transitive compilation-context defines"
#endif
#ifndef PCH_BUILD_OPTION_BIAS
#error "PCH did not inherit command-line C++ build options"
#endif
static_assert(PCH_FIXTURE_BIAS == 37);
static_assert(PCH_DEPENDENCY_BIAS == 5);
static_assert(PCH_BUILD_OPTION_BIAS == 11);
