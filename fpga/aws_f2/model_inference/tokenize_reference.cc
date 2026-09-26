#include <iostream>
#include <iterator>
#include <string>
#include <vector>

#include "llama.h"
int main(int argc, char **argv) {
  if (argc != 2)
    return 2;
  auto params       = llama_model_default_params();
  params.vocab_only = true;
  auto *model       = llama_model_load_from_file(argv[1], params);
  if (!model)
    return 3;
  auto *vocab = llama_model_get_vocab(model);
  std::string input((std::istreambuf_iterator<char>(std::cin)), {});
  std::vector<llama_token> ids(input.size() + 64);
  int n = llama_tokenize(vocab, input.data(), input.size(), ids.data(), ids.size(), false, true);
  if (n < 0)
    return 4;
  std::cout << '[';
  for (int i = 0; i < n; ++i)
    std::cout << (i ? "," : "") << ids[i];
  std::cout << "]\n";
  llama_model_free(model);
}
