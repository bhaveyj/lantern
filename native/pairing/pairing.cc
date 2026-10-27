// Lantern's bounded C/JNI bindings to BoringSSL's RFC 9383 implementation.
#include <cstdint>
#include <memory>
#include <new>
#include <jni.h>
#include <openssl/mem.h>
#include "crypto/spake2plus/internal.h"

#ifdef _WIN32
#define LP_EXPORT extern "C" __declspec(dllexport)
#else
#define LP_EXPORT extern "C" __attribute__((visibility("default")))
#endif

namespace {
using bssl::Span;
namespace sp = bssl::spake2plus;
struct Session {
  std::unique_ptr<sp::Prover> prover;
  std::unique_ptr<sp::Verifier> verifier;
};
constexpr uint8_t kProver[] = "lantern-windows-v3";
constexpr uint8_t kVerifier[] = "lantern-tv-v3";
}

LP_EXPORT void* lp_new(int role, const uint8_t* pin, size_t pin_size,
                       const uint8_t* context, size_t context_size) {
  if (!pin || pin_size != 6 || !context || context_size > 1024 ||
      (role != 0 && role != 1)) return nullptr;
  for (size_t i = 0; i < pin_size; ++i) if (pin[i] < '0' || pin[i] > '9') return nullptr;
  uint8_t w0[32], w1[32], record[65];
  const Span<const uint8_t> id_p(kProver, sizeof(kProver) - 1);
  const Span<const uint8_t> id_v(kVerifier, sizeof(kVerifier) - 1);
  bool valid = sp::Register(w0, w1, record, Span(pin, pin_size), id_p, id_v);
  std::unique_ptr<Session> session(new (std::nothrow) Session());
  if (valid && session) {
    if (role == 0) {
      session->prover.reset(new (std::nothrow) sp::Prover());
      valid = session->prover && session->prover->Init(Span(context, context_size), id_p, id_v, w0, w1);
    } else {
      session->verifier.reset(new (std::nothrow) sp::Verifier());
      valid = session->verifier && session->verifier->Init(Span(context, context_size), id_p, id_v, w0, record);
    }
  }
  OPENSSL_cleanse(w0, sizeof(w0));
  OPENSSL_cleanse(w1, sizeof(w1));
  OPENSSL_cleanse(record, sizeof(record));
  return valid && session ? session.release() : nullptr;
}

LP_EXPORT int lp_step(void* handle, int operation, const uint8_t* input,
                      size_t input_size, uint8_t* output, size_t output_size) {
  auto* session = static_cast<Session*>(handle);
  if (!session || (input_size && !input) || (output_size && !output)) return 0;
  if (operation == 0 && session->prover && input_size == 0 && output_size == 65)
    return session->prover->GenerateShare(Span(output, 65));
  if (operation == 1 && session->verifier && input_size == 65 && output_size == 129)
    return session->verifier->ProcessProverShare(Span(output, 65), Span(output + 65, 32), Span(output + 97, 32), Span(input, 65));
  if (operation == 2 && session->prover && input_size == 97 && output_size == 64)
    return session->prover->ComputeConfirmation(Span(output, 32), Span(output + 32, 32), Span(input, 65), Span(input + 65, 32));
  if (operation == 3 && session->verifier && input_size == 32 && output_size == 0)
    return session->verifier->VerifyProverConfirmation(Span(input, 32));
  return 0;
}

LP_EXPORT void lp_free(void* handle) { delete static_cast<Session*>(handle); }

extern "C" JNIEXPORT jlong JNICALL
Java_app_lantern_tv_NativePake_create(JNIEnv* env, jobject, jint role,
                                     jbyteArray pin, jbyteArray context) {
  if (!pin || !context || env->GetArrayLength(pin) != 6 || env->GetArrayLength(context) > 1024) return 0;
  uint8_t pin_bytes[6], context_bytes[1024];
  size_t size = env->GetArrayLength(context);
  env->GetByteArrayRegion(pin, 0, 6, reinterpret_cast<jbyte*>(pin_bytes));
  env->GetByteArrayRegion(context, 0, size, reinterpret_cast<jbyte*>(context_bytes));
  void* handle = lp_new(role, pin_bytes, 6, context_bytes, size);
  OPENSSL_cleanse(pin_bytes, sizeof(pin_bytes));
  return reinterpret_cast<jlong>(handle);
}

extern "C" JNIEXPORT jbyteArray JNICALL
Java_app_lantern_tv_NativePake_step(JNIEnv* env, jobject, jlong handle,
                                   jint operation, jbyteArray input) {
  if (!input || env->GetArrayLength(input) > 97) return nullptr;
  size_t output_size;
  switch (operation) { case 0: output_size = 65; break; case 1: output_size = 129; break;
    case 2: output_size = 64; break; case 3: output_size = 0; break; default: return nullptr; }
  uint8_t incoming[97], outgoing[129];
  size_t input_size = env->GetArrayLength(input);
  env->GetByteArrayRegion(input, 0, input_size, reinterpret_cast<jbyte*>(incoming));
  bool success = lp_step(reinterpret_cast<void*>(handle), operation, incoming, input_size, outgoing, output_size);
  jbyteArray result = success ? env->NewByteArray(output_size) : nullptr;
  if (result) env->SetByteArrayRegion(result, 0, output_size, reinterpret_cast<jbyte*>(outgoing));
  OPENSSL_cleanse(outgoing, sizeof(outgoing));
  return result;
}

extern "C" JNIEXPORT void JNICALL
Java_app_lantern_tv_NativePake_destroy(JNIEnv*, jobject, jlong handle) {
  lp_free(reinterpret_cast<void*>(handle));
}
