# References and provenance

1. Zou et al., [Hybrid Reasoning Agents, arXiv:2610.18861v1](https://arxiv.org/abs/2610.18861v1),
   submitted 2026-10-13. Its transitions between latent reasoning and text
   interaction motivate which state a continuation must preserve. This project
   does not reproduce its benchmark results.
2. Together Computer,
   [HybridAgent commit 1b41cebbd2388d083ee1820af421b7f9e24affb5](https://github.com/togethercomputer/HybridAgent/tree/1b41cebbd2388d083ee1820af421b7f9e24affb5).
   Inspected engine blob: `9614f09d9861bf80ffa23b9f2a11e7526d75925f`. That
   inspection informed the state inventory. No upstream model, weights, source
   files or data are bundled here. Real-engine execution is NOT RUN.
3. [Safetensors documentation](https://huggingface.co/docs/safetensors/index)
   describes the optional probe's tensor serialization format.
4. [Setuptools: controlling distribution files](https://setuptools.pypa.io/en/latest/userguide/miscellaneous.html)
   describes the sdist manifest and wheel boundary used here.

Toy transitions, numerical probe and test inputs are locally authored. Torch,
safetensors, NumPy and packaging are separately installed public dependencies with
their own licences. No external corpus, pretrained weights, paper text or
tool-service output is redistributed. Original code is MIT licensed, copyright
2026 Jason Lovell. See [LICENSE](../LICENSE).
