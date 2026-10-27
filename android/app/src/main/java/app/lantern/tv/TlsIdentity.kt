package app.lantern.tv

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.math.BigInteger
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.MessageDigest
import java.security.spec.ECGenParameterSpec
import java.util.Date
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.security.auth.x500.X500Principal

class TlsIdentity {
    val context: SSLContext
    val fingerprint: String

    init {
        // Conscrypt signs TLS's already-hashed ECDSA challenges with
        // NONEwithECDSA. A fresh alias migrates keys lacking that permission
        // without deleting the previous identity or clearing application data.
        val alias = "lantern-control-tls-v2-prehashed"
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (!store.containsAlias(alias)) {
            val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
            generator.initialize(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_NONE, KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_SHA384, KeyProperties.DIGEST_SHA512)
                .setCertificateSubject(X500Principal("CN=Lantern TV"))
                .setCertificateSerialNumber(BigInteger.valueOf(System.currentTimeMillis()))
                .setCertificateNotBefore(Date(System.currentTimeMillis() - 86400000L))
                .setCertificateNotAfter(Date(System.currentTimeMillis() + 315360000000L)).build())
            generator.generateKeyPair()
        }
        val managers = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
        managers.init(store, null)
        context = SSLContext.getInstance("TLS").apply { init(managers.keyManagers, null, null) }
        fingerprint = MessageDigest.getInstance("SHA-256").digest(store.getCertificate(alias).encoded).joinToString("") { "%02x".format(it) }
    }
}
