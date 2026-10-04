"""Generate a public-IO wrapper; retain the unmodified shared C++ oracle."""
import argparse
import json
from pathlib import Path

PORTS = {
    'clk': ('input', 1),
    'reset': ('input', 1),
    's_araddr': ('input', 32),
    's_arid': ('input', 6),
    's_arlen': ('input', 8),
    's_arsize': ('input', 3),
    's_arvalid': ('input', 1),
    's_arready': ('output', 1),
    's_rdata': ('output', 128),
    's_rid': ('output', 6),
    's_rresp': ('output', 2),
    's_rlast': ('output', 1),
    's_rvalid': ('output', 1),
    's_rready': ('input', 1),
    's_awaddr': ('input', 32),
    's_awid': ('input', 6),
    's_awlen': ('input', 8),
    's_awsize': ('input', 3),
    's_awvalid': ('input', 1),
    's_awready': ('output', 1),
    's_wdata': ('input', 128),
    's_wstrb': ('input', 16),
    's_wlast': ('input', 1),
    's_wvalid': ('input', 1),
    's_wready': ('output', 1),
    's_bid': ('output', 6),
    's_bresp': ('output', 2),
    's_bvalid': ('output', 1),
    's_bready': ('input', 1),
    'storage_valid': ('output', 1),
    'storage_ready': ('input', 1),
    'storage_addr': ('output', 32),
    'storage_rsp_valid': ('input', 1),
    'storage_rsp_error': ('input', 1),
    'storage_rsp_data': ('input', 128),
    'storage_rsp_ready': ('output', 1),
}


def wrapper(path, parameters):
    declarations = [
        f'  {direction} logic ' + (f'[{bits - 1}:0] ' if bits > 1 else '') +
        name for name, (direction, bits) in PORTS.items()
    ]
    settings = {
        'DATA_BITS': 128,
        'ID_BITS': 6,
        'ENGINE_ENABLE': 1,
        'ROM_BYTES': 1048576,
        **parameters
    }
    text = 'module coral_weight_ci_top (\n' + ',\n'.join(
        declarations
    ) + '\n);\n'
    text += '  coral_weight_axi #(\n' + ',\n'.join(
        f'    .{name}({value})' for name, value in settings.items()
    ) + '\n  ) dut (\n'
    text += ',\n'.join(
        f'    .{name}({name})' for name in PORTS
    ) + '\n  );\nendmodule\n'
    path.write_text(text)


def adapter(state, output):
    models = json.loads(state.read_text())
    if len(models) != 1 or models[0]['name'] != 'coral_weight_ci_top':
        raise ValueError('expected one coral_weight_ci_top model')
    ports = {
        p['name']: p
        for p in models[0]['states']
        if p['type'] in ('input', 'output')
    }
    if len(ports) != len(PORTS) or set(ports) != set(PORTS):
        raise ValueError(
            'generated public IO differs from the pinned contract'
        )
    declarations, initializers = [], []
    for name, (direction, bits) in PORTS.items():
        p = ports[name]
        if p['type'] != direction or p['numBits'] != bits:
            raise ValueError('generated IO width/direction mismatch: ' + name)
        typ = 'ArcWords' if bits == 128 else f'ArcPort<uint{8 if bits <= 8 else 16 if bits <= 16 else 32}_t>'
        declarations.append(f'  {typ} {name};')
        kind = 'Input' if direction == 'input' else 'Output'
        initializers.append(
            f'    {name}(port("{name}", {bits}, Signal::{kind}), {bits})'
        )
    code = r'''// Uses public generated IO only; no internal state manipulation.
#pragma once
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include "coral_weight_ci_top.h"
template <class T> class ArcPort {
  uint8_t *data;
  unsigned bits;
public:
  ArcPort(uint8_t *p, unsigned b) : data(p), bits(b) {}
  operator T() const {
    T value = 0;
    std::memcpy(&value, data, (bits + 7) / 8);
    return T(uint64_t(value) & ((uint64_t(1) << bits) - 1));
  }
  ArcPort &operator=(const ArcPort &other) { return *this = T(other); }
  ArcPort &operator=(T value) {
    value = T(uint64_t(value) & ((uint64_t(1) << bits) - 1));
    std::memcpy(data, &value, (bits + 7) / 8);
    return *this;
  }
};
class ArcWords {
  uint8_t *data;
public:
  ArcWords(uint8_t *p, unsigned bits) : data(p) {
    if (bits != 128) throw std::runtime_error("expected 128-bit IO");
  }
  ArcPort<uint32_t> operator[](unsigned index) {
    if (index >= 4) throw std::runtime_error("wide IO index out of bounds");
    return ArcPort<uint32_t>(data + 4 * index, 32);
  }
};
class Vcoral_weight_axi {
  coral_weight_ci_top model;
  uint8_t *port(const char *name, unsigned bits, Signal::Type direction) {
    for (const auto &p : coral_weight_ci_topLayout::io) {
      if (std::string(p.name) == name) {
        if (p.numBits != bits || p.type != direction ||
            p.offset + (bits + 7) / 8 > model.storage.size())
          throw std::runtime_error("invalid public IO metadata");
        return model.storage.data() + p.offset;
      }
    }
    throw std::runtime_error("public IO missing");
  }
public:
'''
    code += '\n'.join(declarations) + '\n  Vcoral_weight_axi() :\n'
    code += ',\n'.join(
        initializers
    ) + ' {}\n  void eval() { model.eval(); }\n};\n'
    (output / 'Vcoral_weight_axi.h').write_text(code)
    (output / 'verilated.h').write_text(
        '#pragma once\nstruct Verilated { static void commandArgs(int, char **) {} };\n'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--parameter', action='append', default=[])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.state:
        adapter(args.state, args.output)
    else:
        parameters = {}
        for item in args.parameter:
            key, value = item.split('=', 1)
            if key not in ('FETCH_DEPTH', 'DOT_LANES') or key in parameters:
                parser.error('invalid or duplicate variant parameter')
            parameters[key] = int(value)
        wrapper(args.output / 'top.sv', parameters)


if __name__ == '__main__':
    main()
