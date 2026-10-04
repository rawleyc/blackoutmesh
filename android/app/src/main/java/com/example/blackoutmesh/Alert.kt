package com.example.blackoutmesh

import org.bouncycastle.crypto.params.Ed25519PublicKeyParameters
import org.bouncycastle.crypto.signers.Ed25519Signer
import java.nio.ByteBuffer
import java.nio.ByteOrder

data class Alert(
    val msgId: Long,
    val issuedAt: Long,      // unix seconds
    val validMinutes: Int,
    val template: Int,
    val param: Int,
    val raw: ByteArray       // the exact 79 bytes, forwarded unchanged
) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (javaClass != other?.javaClass) return false
        other as Alert
        return msgId == other.msgId && issuedAt == other.issuedAt
    }

    override fun hashCode(): Int {
        var result = msgId.hashCode()
        result = 31 * result + issuedAt.hashCode()
        return result
    }
}

object AlertCodec {
    private const val PAYLOAD_LEN = 15
    private const val SIG_LEN = 64

    fun hex(s: String): ByteArray =
        s.chunked(2).map { it.toInt(16).toByte() }.toByteArray()

    sealed class VerificationResult {
        data class Valid(val alert: Alert) : VerificationResult()
        data class InvalidLength(val actual: Int, val expected: Int) : VerificationResult()
        object InvalidSignature : VerificationResult()
        data class InvalidVersion(val version: Int) : VerificationResult()
        data class Expired(val issuedAt: Long, val validMinutes: Int, val nowSec: Long) : VerificationResult()
    }

    /** Returns detailed verification status to pinpoint exact rejection causes. */
    fun verifyDetailed(
        raw: ByteArray,
        nowSec: Long = System.currentTimeMillis() / 1000
    ): VerificationResult {
        if (raw.size != PAYLOAD_LEN + SIG_LEN) {
            return VerificationResult.InvalidLength(raw.size, PAYLOAD_LEN + SIG_LEN)
        }

        val payload = raw.copyOfRange(0, PAYLOAD_LEN)
        val sig = raw.copyOfRange(PAYLOAD_LEN, raw.size)
        if (!signatureOk(payload, sig)) {
            return VerificationResult.InvalidSignature
        }

        val b = ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN)
        val version = b.get().toInt() and 0xFF
        if (version != 1) {
            return VerificationResult.InvalidVersion(version)
        }

        val msgId = b.int.toLong() and 0xFFFFFFFFL
        val issuedAt = b.int.toLong() and 0xFFFFFFFFL
        val valid = b.short.toInt() and 0xFFFF
        val template = b.short.toInt() and 0xFFFF
        val param = b.short.toInt() and 0xFFFF

        if (nowSec > issuedAt + valid * 60L) {
            return VerificationResult.Expired(issuedAt, valid, nowSec)
        }

        return VerificationResult.Valid(Alert(msgId, issuedAt, valid, template, param, raw))
    }

    /** Returns the alert if the packet is well formed, signed by our authority, and not expired. */
    fun verifyAndParse(
        raw: ByteArray,
        nowSec: Long = System.currentTimeMillis() / 1000
    ): Alert? = when (val res = verifyDetailed(raw, nowSec)) {
        is VerificationResult.Valid -> res.alert
        else -> null
    }

    private fun signatureOk(payload: ByteArray, sig: ByteArray): Boolean = try {
        val verifier = Ed25519Signer()
        verifier.init(false, Ed25519PublicKeyParameters(Config.PUBLIC_KEY, 0))
        verifier.update(payload, 0, payload.size)
        verifier.verifySignature(sig)
    } catch (e: Exception) {
        false
    }
}

/** Turns (template, param) into a sentence. Same table must exist on every phone. */
object Codebook {
    private val templates = mapOf(
        1 to "Evacuate now. Go to %s.",
        2 to "Alarm powietrzny / Air raid warning! Take shelter immediately at %s.",
        3 to "Power grid failure. Water & charging station open at %s.",
        4 to "All clear. The emergency has ended. Safe to return home from %s."
    )
    private val places = mapOf(
        1 to "Town Hall (Rynek Główny)",
        2 to "TAURON Arena, Gate 3",
        3 to "Main Railway Station (Dworzec Główny)",
        4 to "Nowa Huta Underground Bunker"
    )

    fun render(a: Alert): String {
        val t = templates[a.template]
            ?: return "Official emergency alert received (Template ${a.template}, Param ${a.param})."
        return if (t.contains("%s")) {
            t.format(places[a.param] ?: "the designated emergency zone")
        } else t
    }
}
