// Known-answer test from RFC 9383 Appendix C (public protocol test values).
#include <cstring>
#include <vector>
#include <openssl/mem.h>
#include "crypto/spake2plus/internal.h"

static std::vector<uint8_t> Decode(const char* text) {
  std::vector<uint8_t> result;
  auto nibble = [](char c) { return c <= '9' ? c - '0' : c - 'a' + 10; };
  for (size_t i = 0; text[i]; i += 2) result.push_back((nibble(text[i]) << 4) | nibble(text[i + 1]));
  return result;
}
int main() {
  using bssl::Span;
  namespace sp = bssl::spake2plus;
  auto w0 = Decode("bb8e1bbcf3c48f62c08db243652ae55d3e5586053fca77102994f23ad95491b3");
  auto w1 = Decode("7e945f34d78785b8a3ef44d0df5a1a97d6b3b460409a345ca7830387a74b1dba");
  auto record = Decode("04eb7c9db3d9a9eb1f8adab81b5794c1f13ae3e225efbe91ea487425854c7fc00f00bfedcbd09b2400142d40a14f2064ef31dfaa903b91d1faea7093d835966efd");
  auto x = Decode("d1232c8e8693d02368976c174e2088851b8365d0d79a9eee709c6a05a2fad539");
  auto y = Decode("717a72348a182085109c8d3917d6c43d59b224dc6a7fc4f0483232fa6516d8b3");
  auto expected = Decode("0c5f8ccd1413423a54f6c1fb26ff01534a87f893779c6e68666d772bfd91f3e7");
  auto expected_p = Decode("926cc713504b9b4d76c9162ded04b5493e89109f6d89462cd33adc46fda27527");
  auto expected_v = Decode("9747bcc4f8fe9f63defee53ac9b07876d907d55047e6ff2def2e7529089d3e68");
  const char* context = "SPAKE2+-P256-SHA256-HKDF-SHA256-HMAC-SHA256 Test Vectors";
  auto bytes = [](const char* text) { return Span(reinterpret_cast<const uint8_t*>(text), strlen(text)); };
  sp::Prover client;
  sp::Verifier server;
  uint8_t a[65], b[65], proof_a[32], proof_b[32], key_a[32], key_b[32];
  if (!client.Init(bytes(context), bytes("client"), bytes("server"), w0, w1, x) ||
      !server.Init(bytes(context), bytes("client"), bytes("server"), w0, record, y) ||
      !client.GenerateShare(a) || !server.ProcessProverShare(b, proof_b, key_b, a) ||
      !client.ComputeConfirmation(proof_a, key_a, b, proof_b) || !server.VerifyProverConfirmation(proof_a)) return 1;
  return CRYPTO_memcmp(key_a, expected.data(), 32) || CRYPTO_memcmp(key_b, expected.data(), 32) ||
         CRYPTO_memcmp(proof_a, expected_p.data(), 32) || CRYPTO_memcmp(proof_b, expected_v.data(), 32);
}
