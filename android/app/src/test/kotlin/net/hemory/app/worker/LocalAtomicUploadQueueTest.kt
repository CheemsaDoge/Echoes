package net.hemory.app.worker

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class LocalAtomicUploadQueueTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    @Test
    fun testEnqueueAndPersistence() {
        val queueFile = File(tempFolder.newFolder("upload"), "upload_queue.json")
        val queue = LocalAtomicUploadQueue(queueFile)

        val entry1 = UploadQueueEntry(
            uploadId = "upl_001",
            recordingId = "rec_001",
            tenantId = "tenant_alpha",
            vaultId = "vault_alpha",
            filePath = "/data/test/seg1.aac",
            metaPath = "/data/test/seg1.meta",
            fileSizeBytes = 1024L,
            createdAtMs = 1_000_000L
        )
        queue.enqueue(entry1)

        assertEquals(1, queue.list().size)
        assertEquals("tenant_alpha", queue.get("upl_001")?.tenantId)
        assertTrue("Queue file must exist on disk", queueFile.exists())

        // Create new queue instance reading from same file
        val queueReloaded = LocalAtomicUploadQueue(queueFile)
        val loadedEntry = queueReloaded.get("upl_001")
        assertNotNull(loadedEntry)
        assertEquals("rec_001", loadedEntry?.recordingId)
        assertEquals(UploadEntryState.Pending, loadedEntry?.status)
    }

    @Test
    fun testUpdateAndRemove() {
        val queueFile = File(tempFolder.newFolder("upload_update"), "upload_queue.json")
        val queue = LocalAtomicUploadQueue(queueFile)

        val entry = UploadQueueEntry(
            uploadId = "upl_002",
            recordingId = "rec_002",
            tenantId = "tenant_alpha",
            vaultId = "vault_alpha",
            filePath = "/path/test.aac",
            metaPath = "",
            fileSizeBytes = 500L,
            createdAtMs = 1_000_000L
        )
        queue.enqueue(entry)

        // Update state to Uploading
        queue.update("upl_002") {
            it.copy(status = UploadEntryState.Uploading, retryCount = 1)
        }
        assertEquals(UploadEntryState.Uploading, queue.get("upl_002")?.status)
        assertEquals(1, queue.get("upl_002")?.retryCount)

        // Remove
        assertTrue(queue.remove("upl_002"))
        assertNull(queue.get("upl_002"))
        assertEquals(0, queue.list().size)
    }

    @Test
    fun testCorruptionSelfHealing() {
        val folder = tempFolder.newFolder("upload_corrupt")
        val queueFile = File(folder, "upload_queue.json")

        // Write syntactically malformed JSON
        queueFile.writeText("{\"schemaVersion\": 1, \"entries\": [ { \"incomplete_json_missing_close")

        var testClock = 2_000_000L
        val queue = LocalAtomicUploadQueue(queueFile, clock = { testClock })

        // Queue should self-heal and reset to empty
        assertEquals(0, queue.list().size)

        // Corrupted file backup should exist
        val corruptBackup = File(folder, "upload_queue.json.corrupt.2000000")
        assertTrue("Corrupt backup file must be created", corruptBackup.exists())

        // Active queue file should be valid empty JSON
        assertTrue("Queue file must be recreated", queueFile.exists())
        val content = queueFile.readText()
        assertTrue(content.contains("\"schemaVersion\": 1"))
        assertTrue(content.contains("\"entries\": []"))
    }

    @Test
    fun test7DayRetentionPruning() {
        val folder = tempFolder.newFolder("upload_prune")
        val queueFile = File(folder, "upload_queue.json")
        val nowMs = 1_000_000_000L
        val eightDaysAgo = nowMs - (8 * 86_400_000L)
        val twoDaysAgo = nowMs - (2 * 86_400_000L)
        val tenDaysAgo = nowMs - (10 * 86_400_000L)

        // Create dummy physical files for entries
        val oldAac = File(folder, "old.aac").apply { writeText("audio_bytes_old") }
        val oldMeta = File(folder, "old.meta").apply { writeText("meta_bytes_old") }
        val recentAac = File(folder, "recent.aac").apply { writeText("audio_bytes_recent") }
        val pendingAac = File(folder, "pending.aac").apply { writeText("audio_bytes_pending") }

        val queue = LocalAtomicUploadQueue(queueFile, clock = { nowMs })

        // Entry 1: Completed and synced, older than 7 days -> SHOULD BE PRUNED
        queue.enqueue(UploadQueueEntry(
            uploadId = "upl_old_completed",
            recordingId = "rec_old",
            tenantId = "t1",
            vaultId = "v1",
            filePath = oldAac.absolutePath,
            metaPath = oldMeta.absolutePath,
            fileSizeBytes = 100L,
            createdAtMs = eightDaysAgo,
            status = UploadEntryState.Completed,
            sidecarUploadedSynced = true
        ))

        // Entry 2: Completed, newer than 7 days -> MUST BE RETAINED
        queue.enqueue(UploadQueueEntry(
            uploadId = "upl_recent_completed",
            recordingId = "rec_recent",
            tenantId = "t1",
            vaultId = "v1",
            filePath = recentAac.absolutePath,
            metaPath = "",
            fileSizeBytes = 100L,
            createdAtMs = twoDaysAgo,
            status = UploadEntryState.Completed,
            sidecarUploadedSynced = true
        ))

        // Entry 3: Pending, older than 7 days -> MUST BE RETAINED (not uploaded yet)
        queue.enqueue(UploadQueueEntry(
            uploadId = "upl_old_pending",
            recordingId = "rec_pending",
            tenantId = "t1",
            vaultId = "v1",
            filePath = pendingAac.absolutePath,
            metaPath = "",
            fileSizeBytes = 100L,
            createdAtMs = tenDaysAgo,
            status = UploadEntryState.Pending
        ))

        // Entry 4: Created 10 days ago, but uploaded 2 days ago -> MUST BE RETAINED
        val delayedUploadedAac = File(folder, "delayed.aac").apply { writeText("audio_delayed") }
        queue.enqueue(UploadQueueEntry(
            uploadId = "upl_delayed_uploaded",
            recordingId = "rec_delayed",
            tenantId = "t1",
            vaultId = "v1",
            filePath = delayedUploadedAac.absolutePath,
            metaPath = "",
            fileSizeBytes = 100L,
            createdAtMs = tenDaysAgo,
            lastAttemptAtMs = twoDaysAgo,
            status = UploadEntryState.Completed,
            sidecarUploadedSynced = true
        ))

        // Entry 5: Completed older than 7 days but sidecar not synced -> MUST BE RETAINED
        val unsyncedAac = File(folder, "unsynced.aac").apply { writeText("audio_unsynced") }
        queue.enqueue(UploadQueueEntry(
            uploadId = "upl_old_completed_unsynced",
            recordingId = "rec_unsynced",
            tenantId = "t1",
            vaultId = "v1",
            filePath = unsyncedAac.absolutePath,
            metaPath = "",
            fileSizeBytes = 100L,
            createdAtMs = eightDaysAgo,
            status = UploadEntryState.Completed,
            sidecarUploadedSynced = false
        ))

        assertEquals(5, queue.list().size)

        // Execute pruning with cutoff at now - 7 days
        val pruned = queue.pruneCompleted(cutoffMs = nowMs - (7 * 86_400_000L))
        assertEquals(listOf("upl_old_completed"), pruned)

        // Entry 1 should be gone from queue and disk
        assertNull(queue.get("upl_old_completed"))
        assertFalse("Old audio file must be purged from disk", oldAac.exists())
        assertFalse("Old meta file must be purged from disk", oldMeta.exists())

        // Entries 2, 3, 4 and 5 must still exist in queue and on disk
        assertNotNull(queue.get("upl_recent_completed"))
        assertNotNull(queue.get("upl_old_pending"))
        assertNotNull(queue.get("upl_delayed_uploaded"))
        assertNotNull(queue.get("upl_old_completed_unsynced"))
        assertTrue("Recent audio file must remain", recentAac.exists())
        assertTrue("Pending audio file must remain", pendingAac.exists())
        assertTrue("Delayed uploaded audio file must remain", delayedUploadedAac.exists())
        assertTrue("Unsynced audio file must remain", unsyncedAac.exists())
    }
}
