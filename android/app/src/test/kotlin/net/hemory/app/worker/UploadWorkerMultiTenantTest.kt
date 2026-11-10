package net.hemory.app.worker

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class UploadWorkerMultiTenantTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    @Test
    fun testMultiTenantGuardAuthorizationEvaluation() {
        // Matching credentials
        assertTrue(
            MultiTenantGuard.isAuthorized(
                activeTenantId = "tenant_alpha",
                activeVaultId = "vault_alpha",
                entryTenantId = "tenant_alpha",
                entryVaultId = "vault_alpha"
            )
        )

        // Tenant mismatch
        assertFalse(
            MultiTenantGuard.isAuthorized(
                activeTenantId = "tenant_beta",
                activeVaultId = "vault_alpha",
                entryTenantId = "tenant_alpha",
                entryVaultId = "vault_alpha"
            )
        )

        // Vault mismatch
        assertFalse(
            MultiTenantGuard.isAuthorized(
                activeTenantId = "tenant_alpha",
                activeVaultId = "vault_beta",
                entryTenantId = "tenant_alpha",
                entryVaultId = "vault_alpha"
            )
        )

        // Missing active credentials
        assertFalse(
            MultiTenantGuard.isAuthorized(
                activeTenantId = "",
                activeVaultId = "",
                entryTenantId = "tenant_alpha",
                entryVaultId = "vault_alpha"
            )
        )
        assertFalse(
            MultiTenantGuard.isAuthorized(
                activeTenantId = null,
                activeVaultId = null,
                entryTenantId = "tenant_alpha",
                entryVaultId = "vault_alpha"
            )
        )
    }

    @Test
    fun testMultiTenantMismatchPolicyPreservesAudioFileAndMarksPermanentFailure() {
        val folder = tempFolder.newFolder("upload_mismatch")
        val queueFile = File(folder, "upload_queue.json")
        val audioFile = File(folder, "segment_123.aac").apply { writeText("fake_aac_payload_bytes") }
        val metaFile = File(folder, "segment_123.meta").apply { writeText("{\"schemaVersion\": 1}") }

        val queue = LocalAtomicUploadQueue(queueFile)
        val entry = UploadQueueEntry(
            uploadId = "upl_mismatch_001",
            recordingId = "rec_alpha",
            tenantId = "tenant_alpha",
            vaultId = "vault_alpha",
            filePath = audioFile.absolutePath,
            metaPath = metaFile.absolutePath,
            fileSizeBytes = audioFile.length(),
            createdAtMs = System.currentTimeMillis(),
            status = UploadEntryState.Pending
        )
        queue.enqueue(entry)

        // Simulate identity mismatch check
        val activeTenant = "tenant_attacker_or_switched"
        val activeVault = "vault_beta"

        assertFalse(MultiTenantGuard.isAuthorized(activeTenant, activeVault, entry.tenantId, entry.vaultId))

        // Apply mismatch policy
        MultiTenantGuard.applyMismatchPolicy(queue, entry.uploadId, entry, activeTenant, activeVault)

        // Verify updated entry in queue
        val updated = queue.get(entry.uploadId)
        assertNotNull(updated)
        assertEquals(UploadEntryState.FailedPermanent, updated?.status)
        assertEquals("identity_mismatch", updated?.lastError?.code)
        assertEquals("条目身份与当前登录态不匹配，暂不发送", updated?.lastError?.message)

        // CRITICAL INVARIANT: Local files MUST NOT be deleted
        assertTrue("Audio file MUST be preserved on disk during tenant mismatch", audioFile.exists())
        assertTrue("Meta file MUST be preserved on disk during tenant mismatch", metaFile.exists())
        assertEquals("fake_aac_payload_bytes", audioFile.readText())
    }
}
