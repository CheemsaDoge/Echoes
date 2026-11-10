package net.hemory.app.challenger

import net.hemory.app.native.DeadManSwitchAlarmReceiver
import net.hemory.app.native.WatchdogAction
import net.hemory.app.native.WatchdogDiscriminator
import net.hemory.app.native.ipc.NativeBridgeEvent
import net.hemory.app.native.ipc.RingBuffer
import net.hemory.app.worker.LocalAtomicUploadQueue
import net.hemory.app.worker.MultiTenantGuard
import net.hemory.app.worker.UploadEntryState
import net.hemory.app.worker.UploadError
import net.hemory.app.worker.UploadQueueEntry
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.Random
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * Empirical Adversarial Challenge Test Suite for Milestone M1:
 * - LocalAtomicUploadQueue crash resilience & self-healing
 * - Multi-Tenant Identity Guard & file preservation
 * - 7-Day Retention Pruning Boundary Math & Synced invariants
 * - Watchdog Dead-Man 3-Branch Discriminator & Alert ID
 * - IPC RingBuffer Overflow, Concurrency & FIFO preservation
 */
class M1PersistenceAndWatchdogChallengeTest {

    @get:Rule
    val tempFolder = TemporaryFolder()

    // =========================================================================
    // 1. LocalAtomicUploadQueue Crash Resilience & Corruption Self-Healing
    // =========================================================================

    @Test
    fun test1_1_CrashResilienceTruncatedFiles() {
        val folder = tempFolder.newFolder("queue_truncated")
        val queueFile = File(folder, "upload_queue.json")

        // First populate valid queue
        val baseQueue = LocalAtomicUploadQueue(queueFile)
        for (i in 1..5) {
            baseQueue.enqueue(
                UploadQueueEntry(
                    uploadId = "upl_$i",
                    recordingId = "rec_$i",
                    tenantId = "tenant_alpha",
                    vaultId = "vault_alpha",
                    filePath = "/path/$i.aac",
                    metaPath = "/path/$i.meta",
                    fileSizeBytes = 1024L * i,
                    createdAtMs = 1_000_000L + i
                )
            )
        }
        val fullContent = queueFile.readText(Charsets.UTF_8)
        assertTrue("Queue file must have valid content", fullContent.length > 100)

        // Truncate at various critical offsets: 0, 1, 10, 45, 120 bytes
        val cutPoints = listOf(0, 1, 10, 45, 120)
        for ((idx, cut) in cutPoints.withIndex()) {
            val truncatedFile = File(folder, "upload_queue_cut_$idx.json")
            val truncatedBytes = fullContent.toByteArray(Charsets.UTF_8).copyOf(cut)
            truncatedFile.writeBytes(truncatedBytes)

            var mockClock = 5_000_000L + idx
            val recoveredQueue = LocalAtomicUploadQueue(truncatedFile, clock = { mockClock })

            // In all truncation scenarios, queue must not crash and list() must be clean
            assertEquals("Truncated file at $cut bytes must result in empty entries", 0, recoveredQueue.list().size)

            // Test enqueue on recovered queue
            val newEntry = UploadQueueEntry(
                uploadId = "upl_new_$idx",
                recordingId = "rec_new",
                tenantId = "tenant_alpha",
                vaultId = "vault_alpha",
                filePath = "/path/new.aac",
                metaPath = "",
                fileSizeBytes = 500L,
                createdAtMs = mockClock
            )
            assertTrue("Enqueue must succeed on recovered queue", recoveredQueue.enqueue(newEntry))
            assertEquals(1, recoveredQueue.list().size)
            assertEquals("upl_new_$idx", recoveredQueue.get("upl_new_$idx")?.uploadId)

            // Verify file on disk is valid JSON now
            val contentOnDisk = truncatedFile.readText(Charsets.UTF_8)
            val json = JSONObject(contentOnDisk)
            assertEquals(1, json.optInt("schemaVersion"))
            assertEquals(1, json.optJSONArray("entries")?.length())
        }
    }

    @Test
    fun test1_2_ChaosRandomBitFlipsFuzzing() {
        val folder = tempFolder.newFolder("queue_fuzz")
        val queueFile = File(folder, "upload_queue.json")

        val baseQueue = LocalAtomicUploadQueue(queueFile)
        for (i in 1..8) {
            baseQueue.enqueue(
                UploadQueueEntry(
                    uploadId = "fuzz_upl_$i",
                    recordingId = "rec_$i",
                    tenantId = "tenant_fuzz",
                    vaultId = "vault_fuzz",
                    filePath = "/tmp/audio_$i.aac",
                    metaPath = "/tmp/meta_$i.meta",
                    fileSizeBytes = 2048L,
                    createdAtMs = 2_000_000L + i
                )
            )
        }
        val originalBytes = queueFile.readBytes()
        val random = Random(42) // Deterministic seed

        // Perform 30 adversarial bit-flip / byte-corruption mutations
        for (iter in 1..30) {
            val mutatedBytes = originalBytes.copyOf()
            val corruptionCount = random.nextInt(5) + 1
            for (c in 0 until corruptionCount) {
                val targetPos = random.nextInt(mutatedBytes.size)
                when (random.nextInt(4)) {
                    0 -> mutatedBytes[targetPos] = 0x00.toByte() // Null byte injection
                    1 -> mutatedBytes[targetPos] = 0xFF.toByte() // Inverted byte
                    2 -> mutatedBytes[targetPos] = random.nextInt(256).toByte() // Random byte
                    3 -> mutatedBytes[targetPos] = '{'.code.toByte() // Syntax breaker
                }
            }

            val testFile = File(folder, "fuzz_iter_$iter.json")
            testFile.writeBytes(mutatedBytes)

            var mockClock = 3_000_000L + iter
            val fuzzedQueue = LocalAtomicUploadQueue(testFile, clock = { mockClock })

            // Fuzzed queue MUST NEVER throw uncaught exception
            assertNotNull(fuzzedQueue.list())

            // Subsequent write operation MUST succeed and self-heal
            val recoverEntry = UploadQueueEntry(
                uploadId = "upl_healed_$iter",
                recordingId = "rec_healed",
                tenantId = "t",
                vaultId = "v",
                filePath = "/a.aac",
                metaPath = "",
                fileSizeBytes = 10L,
                createdAtMs = mockClock
            )
            assertTrue("Enqueue must succeed on fuzzed queue", fuzzedQueue.enqueue(recoverEntry))
            assertEquals(recoverEntry.uploadId, fuzzedQueue.get(recoverEntry.uploadId)?.uploadId)
        }
    }

    @Test
    fun test1_3_CrashMidWriteTmpFileSemantics() {
        val folder = tempFolder.newFolder("queue_crash_tmp")
        val queueFile = File(folder, "upload_queue.json")
        val tmpFile = File(folder, "upload_queue.json.tmp")

        // Scenario A: target queue does NOT exist, but 0-byte tmpFile exists (crash right after FileOutputStream creation)
        tmpFile.writeBytes(ByteArray(0))
        assertFalse(queueFile.exists())
        assertTrue(tmpFile.exists())

        val queueA = LocalAtomicUploadQueue(queueFile)
        assertEquals("Queue should load 0 entries", 0, queueA.list().size)

        // Scenario B: target queue does NOT exist, but corrupt tmpFile exists
        queueFile.delete()
        tmpFile.writeText("{\"schemaVersion\": 1, \"entries\": [ broken_incomplete")
        val queueB = LocalAtomicUploadQueue(queueFile, clock = { 7_777_777L })
        assertEquals("Corrupt tmp must be cleaned to empty entries", 0, queueB.list().size)
        val backupFile = File(folder, "upload_queue.json.corrupt.7777777")
        assertTrue("Corrupt backup must be created from restored tmp", backupFile.exists())

        // Scenario C: target queue EXISTS with valid entries, and a stale 0-byte/corrupt tmpFile exists (crash during update)
        queueFile.delete()
        tmpFile.delete()
        val queueC = LocalAtomicUploadQueue(queueFile)
        queueC.enqueue(
            UploadQueueEntry(
                uploadId = "upl_intact",
                recordingId = "rec_intact",
                tenantId = "tenant_valid",
                vaultId = "vault_valid",
                filePath = "/p.aac",
                metaPath = "",
                fileSizeBytes = 100L,
                createdAtMs = 12345L
            )
        )
        // Now inject stale tmp file simulating mid-write crash of next operation
        tmpFile.writeText("GARBAGE_TMP_CONTENT_FROM_CRASHED_WRITE")

        // Reload queue
        val queueReloaded = LocalAtomicUploadQueue(queueFile)
        assertEquals("Target valid queue must NOT be destroyed by stale tmp file", 1, queueReloaded.list().size)
        assertEquals("upl_intact", queueReloaded.get("upl_intact")?.uploadId)

        // Enqueueing a new entry should overwrite stale tmp without throwing
        queueReloaded.enqueue(
            UploadQueueEntry(
                uploadId = "upl_intact_2",
                recordingId = "rec_intact_2",
                tenantId = "tenant_valid",
                vaultId = "vault_valid",
                filePath = "/p2.aac",
                metaPath = "",
                fileSizeBytes = 200L,
                createdAtMs = 12346L
            )
        )
        assertEquals(2, queueReloaded.list().size)
        assertNotNull(queueReloaded.get("upl_intact_2"))
    }

    @Test
    fun test1_4_HighConcurrencyQueueStress() {
        val folder = tempFolder.newFolder("queue_concurrent")
        val queueFile = File(folder, "upload_queue.json")
        val queue = LocalAtomicUploadQueue(queueFile)

        val threadCount = 12
        val opsPerThread = 40
        val executor = Executors.newFixedThreadPool(threadCount)
        val latch = CountDownLatch(threadCount)
        val errorCounter = AtomicInteger(0)

        for (t in 0 until threadCount) {
            executor.submit {
                try {
                    for (i in 0 until opsPerThread) {
                        val uid = "thread_${t}_op_$i"
                        queue.enqueue(
                            UploadQueueEntry(
                                uploadId = uid,
                                recordingId = "rec_$t",
                                tenantId = "tenant_$t",
                                vaultId = "vault_$t",
                                filePath = "/audio/$uid.aac",
                                metaPath = "",
                                fileSizeBytes = 100L,
                                createdAtMs = System.currentTimeMillis()
                            )
                        )
                        // Read back
                        val fetched = queue.get(uid)
                        if (fetched == null || fetched.uploadId != uid) {
                            errorCounter.incrementAndGet()
                        }
                        // Update
                        queue.update(uid) { it.copy(retryCount = it.retryCount + 1) }
                    }
                } catch (e: Throwable) {
                    e.printStackTrace()
                    errorCounter.incrementAndGet()
                } finally {
                    latch.countDown()
                }
            }
        }

        assertTrue("Concurrent ops must finish within 10s", latch.await(10, TimeUnit.SECONDS))
        executor.shutdown()
        assertEquals("Zero concurrency errors or race conditions allowed", 0, errorCounter.get())

        val totalExpected = threadCount * opsPerThread
        assertEquals(totalExpected, queue.list().size)

        // Verify persisted file is clean JSON
        val content = queueFile.readText(Charsets.UTF_8)
        val json = JSONObject(content)
        assertEquals(totalExpected, json.getJSONArray("entries").length())
    }

    // =========================================================================
    // 2. Multi-Tenant Identity Guard & File Preservation
    // =========================================================================

    @Test
    fun test2_1_MultiTenantAuthorizationMatrix() {
        // Full Cartesian authorization evaluation
        assertTrue(MultiTenantGuard.isAuthorized("t1", "v1", "t1", "v1"))

        // Mismatches
        assertFalse("Tenant mismatch must reject", MultiTenantGuard.isAuthorized("t2", "v1", "t1", "v1"))
        assertFalse("Vault mismatch must reject", MultiTenantGuard.isAuthorized("t1", "v2", "t1", "v1"))
        assertFalse("Both mismatch must reject", MultiTenantGuard.isAuthorized("t2", "v2", "t1", "v1"))

        // Null / Empty active auth
        assertFalse("Null active tenant must reject", MultiTenantGuard.isAuthorized(null, "v1", "t1", "v1"))
        assertFalse("Null active vault must reject", MultiTenantGuard.isAuthorized("t1", null, "t1", "v1"))
        assertFalse("Empty active tenant must reject", MultiTenantGuard.isAuthorized("", "v1", "t1", "v1"))
        assertFalse("Empty active vault must reject", MultiTenantGuard.isAuthorized("t1", "", "t1", "v1"))

        // Empty entry fields
        assertFalse("Mismatched empty entry tenant must reject", MultiTenantGuard.isAuthorized("t1", "v1", "", "v1"))
        assertFalse("Mismatched empty entry vault must reject", MultiTenantGuard.isAuthorized("t1", "v1", "t1", ""))
    }

    @Test
    fun test2_2_MultiTenantMismatchPolicyPreservation() {
        val folder = tempFolder.newFolder("guard_mismatch")
        val queueFile = File(folder, "upload_queue.json")
        val queue = LocalAtomicUploadQueue(queueFile)

        val audioFile = File(folder, "tenant_mismatch.aac").apply { writeBytes(byteArrayOf(1, 2, 3, 4, 5)) }
        val metaFile = File(folder, "tenant_mismatch.meta").apply { writeText("{\"meta\": true}") }

        val entry = UploadQueueEntry(
            uploadId = "upl_guard_001",
            recordingId = "rec_tenant_a",
            tenantId = "tenant_A",
            vaultId = "vault_A",
            filePath = audioFile.absolutePath,
            metaPath = metaFile.absolutePath,
            fileSizeBytes = audioFile.length(),
            createdAtMs = System.currentTimeMillis(),
            status = UploadEntryState.Pending
        )
        queue.enqueue(entry)

        // Apply mismatch policy when active user is switched to tenant_B
        MultiTenantGuard.applyMismatchPolicy(
            queue = queue,
            uploadId = entry.uploadId,
            entry = entry,
            activeTenantId = "tenant_B",
            activeVaultId = "vault_B"
        )

        val updated = queue.get(entry.uploadId)
        assertNotNull(updated)
        assertEquals("Entry must transition to FailedPermanent", UploadEntryState.FailedPermanent, updated?.status)
        assertEquals("identity_mismatch", updated?.lastError?.code)
        assertEquals("条目身份与当前登录态不匹配，暂不发送", updated?.lastError?.message)

        // Strict File Invariant: Files MUST NOT be deleted
        assertTrue("Audio file must remain physically on disk", audioFile.exists())
        assertTrue("Meta file must remain physically on disk", metaFile.exists())
        assertArrayEquals(byteArrayOf(1, 2, 3, 4, 5), audioFile.readBytes())
    }

    @Test
    fun test2_3_ConcurrentMultiTenantEvaluation() {
        val folder = tempFolder.newFolder("guard_concurrent")
        val queueFile = File(folder, "upload_queue.json")
        val queue = LocalAtomicUploadQueue(queueFile)

        val count = 20
        val files = ArrayList<File>()
        for (i in 0 until count) {
            val f = File(folder, "audio_$i.aac").apply { writeText("content_$i") }
            files.add(f)
            queue.enqueue(
                UploadQueueEntry(
                    uploadId = "upl_concurrent_tenant_$i",
                    recordingId = "rec_$i",
                    tenantId = "tenant_$i",
                    vaultId = "vault_$i",
                    filePath = f.absolutePath,
                    metaPath = "",
                    fileSizeBytes = f.length(),
                    createdAtMs = System.currentTimeMillis()
                )
            )
        }

        val executor = Executors.newFixedThreadPool(8)
        val latch = CountDownLatch(count)

        for (i in 0 until count) {
            executor.submit {
                try {
                    val entry = queue.get("upl_concurrent_tenant_$i")!!
                    MultiTenantGuard.applyMismatchPolicy(
                        queue = queue,
                        uploadId = entry.uploadId,
                        entry = entry,
                        activeTenantId = "active_single_tenant",
                        activeVaultId = "active_single_vault"
                    )
                } finally {
                    latch.countDown()
                }
            }
        }

        assertTrue(latch.await(5, TimeUnit.SECONDS))
        executor.shutdown()

        // Verify all 20 entries are marked FailedPermanent and all 20 files are intact
        for (i in 0 until count) {
            val entry = queue.get("upl_concurrent_tenant_$i")
            assertEquals(UploadEntryState.FailedPermanent, entry?.status)
            assertTrue("File $i must not be deleted", files[i].exists())
        }
    }

    // =========================================================================
    // 3. 7-Day Retention Pruning Math
    // =========================================================================

    @Test
    fun test3_1_RetentionPruningBoundaryMath() {
        val folder = tempFolder.newFolder("retention_boundary")
        val queueFile = File(folder, "upload_queue.json")
        val nowMs = 1_000_000_000L
        val cutoffMs = nowMs - (7 * 86_400_000L) // Exact cutoff

        val queue = LocalAtomicUploadQueue(queueFile, clock = { nowMs })

        val fileOlder = File(folder, "older.aac").apply { writeText("older") }
        val fileExact = File(folder, "exact.aac").apply { writeText("exact") }
        val fileNewer = File(folder, "newer.aac").apply { writeText("newer") }

        // Entry A: Cutoff - 1ms (older than 7 days) -> MUST BE PRUNED
        queue.enqueue(
            UploadQueueEntry(
                uploadId = "upl_older_1ms",
                recordingId = "rec_1",
                tenantId = "t",
                vaultId = "v",
                filePath = fileOlder.absolutePath,
                metaPath = "",
                fileSizeBytes = 10L,
                createdAtMs = cutoffMs - 1L,
                lastAttemptAtMs = cutoffMs - 1L,
                status = UploadEntryState.Completed,
                sidecarUploadedSynced = true
            )
        )

        // Entry B: Exactly at Cutoff -> MUST BE RETAINED
        queue.enqueue(
            UploadQueueEntry(
                uploadId = "upl_exact_cutoff",
                recordingId = "rec_2",
                tenantId = "t",
                vaultId = "v",
                filePath = fileExact.absolutePath,
                metaPath = "",
                fileSizeBytes = 10L,
                createdAtMs = cutoffMs,
                lastAttemptAtMs = cutoffMs,
                status = UploadEntryState.Completed,
                sidecarUploadedSynced = true
            )
        )

        // Entry C: Cutoff + 1ms (newer than 7 days) -> MUST BE RETAINED
        queue.enqueue(
            UploadQueueEntry(
                uploadId = "upl_newer_1ms",
                recordingId = "rec_3",
                tenantId = "t",
                vaultId = "v",
                filePath = fileNewer.absolutePath,
                metaPath = "",
                fileSizeBytes = 10L,
                createdAtMs = cutoffMs + 1L,
                lastAttemptAtMs = cutoffMs + 1L,
                status = UploadEntryState.Completed,
                sidecarUploadedSynced = true
            )
        )

        val pruned = queue.pruneCompleted(cutoffMs = cutoffMs)

        assertEquals("Only upl_older_1ms must be pruned", listOf("upl_older_1ms"), pruned)
        assertNull(queue.get("upl_older_1ms"))
        assertFalse("Older file must be deleted from disk", fileOlder.exists())

        assertNotNull("Exact cutoff entry must remain in queue", queue.get("upl_exact_cutoff"))
        assertTrue("Exact cutoff file must remain on disk", fileExact.exists())

        assertNotNull("Newer entry must remain in queue", queue.get("upl_newer_1ms"))
        assertTrue("Newer file must remain on disk", fileNewer.exists())
    }

    @Test
    fun test3_2_RetentionPruningTimestampPrecedence() {
        val folder = tempFolder.newFolder("retention_precedence")
        val queueFile = File(folder, "upload_queue.json")
        val nowMs = 1_000_000_000L
        val cutoffMs = nowMs - (7 * 86_400_000L)

        val queue = LocalAtomicUploadQueue(queueFile, clock = { nowMs })

        val fileA = File(folder, "a.aac").apply { writeText("a") }
        val fileB = File(folder, "b.aac").apply { writeText("b") }
        val fileC = File(folder, "c.aac").apply { writeText("c") }

        // Entry A: Created 10 days ago, but uploaded/attempted 2 days ago -> MUST BE RETAINED
        queue.enqueue(
            UploadQueueEntry(
                uploadId = "upl_old_created_recent_attempt",
                recordingId = "rA",
                tenantId = "t",
                vaultId = "v",
                filePath = fileA.absolutePath,
                metaPath = "",
                fileSizeBytes = 10L,
                createdAtMs = cutoffMs - 100_000_000L,
                lastAttemptAtMs = cutoffMs + 50_000_000L,
                status = UploadEntryState.Completed,
                sidecarUploadedSynced = true
            )
        )

        // Entry B: Created 2 days ago, but attempted 8 days ago (simulated edge) -> MUST BE PRUNED
        queue.enqueue(
            UploadQueueEntry(
                uploadId = "upl_recent_created_old_attempt",
                recordingId = "rB",
                tenantId = "t",
                vaultId = "v",
                filePath = fileB.absolutePath,
                metaPath = "",
                fileSizeBytes = 10L,
                createdAtMs = cutoffMs + 50_000_000L,
                lastAttemptAtMs = cutoffMs - 10_000_000L,
                status = UploadEntryState.Completed,
                sidecarUploadedSynced = true
            )
        )

        // Entry C: lastAttemptAtMs == 0, falls back to createdAtMs (which is older than cutoff) -> MUST BE PRUNED
        queue.enqueue(
            UploadQueueEntry(
                uploadId = "upl_zero_attempt_old_created",
                recordingId = "rC",
                tenantId = "t",
                vaultId = "v",
                filePath = fileC.absolutePath,
                metaPath = "",
                fileSizeBytes = 10L,
                createdAtMs = cutoffMs - 10_000_000L,
                lastAttemptAtMs = 0L,
                status = UploadEntryState.Completed,
                sidecarUploadedSynced = true
            )
        )

        val pruned = queue.pruneCompleted(cutoffMs = cutoffMs)
        assertEquals(listOf("upl_recent_created_old_attempt", "upl_zero_attempt_old_created"), pruned)

        assertNotNull(queue.get("upl_old_created_recent_attempt"))
        assertTrue(fileA.exists())

        assertNull(queue.get("upl_recent_created_old_attempt"))
        assertFalse(fileB.exists())

        assertNull(queue.get("upl_zero_attempt_old_created"))
        assertFalse(fileC.exists())
    }

    @Test
    fun test3_3_IncompletedEntriesNeverPrunedRegardlessOfAge() {
        val folder = tempFolder.newFolder("retention_incompleted")
        val queueFile = File(folder, "upload_queue.json")
        val nowMs = 1_000_000_000L
        val cutoffMs = nowMs - (7 * 86_400_000L)
        val ancientTimestamp = nowMs - (100 * 86_400_000L) // 100 days old

        val queue = LocalAtomicUploadQueue(queueFile, clock = { nowMs })

        val incompletedStates = listOf(
            UploadEntryState.Pending,
            UploadEntryState.Uploading,
            UploadEntryState.FailedRecoverable,
            UploadEntryState.FailedPermanent
        )

        val files = ArrayList<File>()
        for (state in incompletedStates) {
            val audio = File(folder, "ancient_${state.wire}.aac").apply { writeText("audio_${state.wire}") }
            val meta = File(folder, "ancient_${state.wire}.meta").apply { writeText("meta_${state.wire}") }
            files.add(audio)
            files.add(meta)

            queue.enqueue(
                UploadQueueEntry(
                    uploadId = "upl_ancient_${state.wire}",
                    recordingId = "rec_ancient",
                    tenantId = "t",
                    vaultId = "v",
                    filePath = audio.absolutePath,
                    metaPath = meta.absolutePath,
                    fileSizeBytes = audio.length(),
                    createdAtMs = ancientTimestamp,
                    lastAttemptAtMs = ancientTimestamp,
                    status = state,
                    sidecarUploadedSynced = false
                )
            )
        }

        // Run pruning with cutoff at nowMs - 7 days
        val pruned = queue.pruneCompleted(cutoffMs = cutoffMs)
        assertTrue("Incompleted entries must NEVER be pruned regardless of age", pruned.isEmpty())
        assertEquals(4, queue.list().size)

        for (f in files) {
            assertTrue("Physical file ${f.name} must remain intact on disk", f.exists())
        }
    }

    @Test
    fun test3_4_RetentionPruningRequiresSidecarUploadedSynced() {
        val folder = tempFolder.newFolder("retention_synced")
        val queueFile = File(folder, "upload_queue.json")
        val nowMs = 1_000_000_000L
        val cutoffMs = nowMs - (7 * 86_400_000L)

        val queue = LocalAtomicUploadQueue(queueFile, clock = { nowMs })

        val syncedFile = File(folder, "synced.aac").apply { writeText("synced") }
        val unsyncedFile = File(folder, "unsynced.aac").apply { writeText("unsynced") }

        // Entry 1: Completed AND sidecarUploadedSynced == true -> PRUNED
        queue.enqueue(
            UploadQueueEntry(
                uploadId = "upl_completed_and_synced",
                recordingId = "r1",
                tenantId = "t",
                vaultId = "v",
                filePath = syncedFile.absolutePath,
                metaPath = "",
                fileSizeBytes = 10L,
                createdAtMs = cutoffMs - 1000L,
                lastAttemptAtMs = cutoffMs - 1000L,
                status = UploadEntryState.Completed,
                sidecarUploadedSynced = true
            )
        )

        // Entry 2: Completed BUT sidecarUploadedSynced == false -> MUST NOT BE PRUNED
        queue.enqueue(
            UploadQueueEntry(
                uploadId = "upl_completed_but_unsynced",
                recordingId = "r2",
                tenantId = "t",
                vaultId = "v",
                filePath = unsyncedFile.absolutePath,
                metaPath = "",
                fileSizeBytes = 10L,
                createdAtMs = cutoffMs - 1000L,
                lastAttemptAtMs = cutoffMs - 1000L,
                status = UploadEntryState.Completed,
                sidecarUploadedSynced = false
            )
        )

        val pruned = queue.pruneCompleted(cutoffMs = cutoffMs)

        // Adversarial assertion: only the synced entry should be pruned
        assertEquals(listOf("upl_completed_and_synced"), pruned)
        assertNull("Synced entry should be pruned", queue.get("upl_completed_and_synced"))
        assertFalse("Synced file should be deleted", syncedFile.exists())

        assertNotNull("Unsynced entry MUST be preserved until sidecar is synced", queue.get("upl_completed_but_unsynced"))
        assertTrue("Unsynced file MUST remain on disk", unsyncedFile.exists())
    }

    // =========================================================================
    // 4. Watchdog Dead-Man 3-Branch Discriminator
    // =========================================================================

    @Test
    fun test4_1_WatchdogBranch1_HalDeadlockWhenStalenessExceeds45s() {
        // Condition: Service running, audio frame staleness > 45s
        val action1 = WatchdogDiscriminator.evaluate(
            isServiceRunning = true,
            frameStalenessMs = 45_001L,
            staleThresholdMs = 45_000L
        )
        assertEquals(WatchdogAction.HAL_DEADLOCK_RESTART, action1)

        val action2 = WatchdogDiscriminator.evaluate(
            isServiceRunning = true,
            frameStalenessMs = 90_000L,
            staleThresholdMs = 45_000L
        )
        assertEquals(WatchdogAction.HAL_DEADLOCK_RESTART, action2)
    }

    @Test
    fun test4_2_WatchdogBranch2_FalseAlarmRearmWhenStalenessWithin45s() {
        // Condition: Service running, audio frame staleness <= 45s
        val action1 = WatchdogDiscriminator.evaluate(
            isServiceRunning = true,
            frameStalenessMs = 45_000L,
            staleThresholdMs = 45_000L
        )
        assertEquals(WatchdogAction.FALSE_ALARM_REARM, action1)

        val action2 = WatchdogDiscriminator.evaluate(
            isServiceRunning = true,
            frameStalenessMs = 15_000L,
            staleThresholdMs = 45_000L
        )
        assertEquals(WatchdogAction.FALSE_ALARM_REARM, action2)

        val action3 = WatchdogDiscriminator.evaluate(
            isServiceRunning = true,
            frameStalenessMs = 0L,
            staleThresholdMs = 45_000L
        )
        assertEquals(WatchdogAction.FALSE_ALARM_REARM, action3)
    }

    @Test
    fun test4_3_WatchdogBranch3_ServiceDeadAlert() {
        // Condition: Service is not running -> always triggers heads-up alert
        val action1 = WatchdogDiscriminator.evaluate(
            isServiceRunning = false,
            frameStalenessMs = 0L,
            staleThresholdMs = 45_000L
        )
        assertEquals(WatchdogAction.SERVICE_DEAD_ALERT, action1)

        val action2 = WatchdogDiscriminator.evaluate(
            isServiceRunning = false,
            frameStalenessMs = 100_000L,
            staleThresholdMs = 45_000L
        )
        assertEquals(WatchdogAction.SERVICE_DEAD_ALERT, action2)

        // Verify notification constants
        assertEquals("hemory_recording_alerts", DeadManSwitchAlarmReceiver.CHANNEL_ID)
        assertEquals(1214606592, DeadManSwitchAlarmReceiver.NOTIFICATION_ID)
        assertEquals(45_000L, DeadManSwitchAlarmReceiver.AUDIO_FRAME_STALE_THRESHOLD_MS)
    }

    // =========================================================================
    // 5. IPC Circular RingBuffer Overflow & Concurrency
    // =========================================================================

    @Test
    fun test5_1_RingBuffer300EventsOverflow256() {
        val buffer = RingBuffer<NativeBridgeEvent>(256)

        // Push 300 sequential events
        for (i in 0 until 300) {
            buffer.push(
                NativeBridgeEvent(
                    sequence = i,
                    event = "AUDIO_SLICE_$i",
                    payload = mapOf("seq" to i)
                )
            )
        }

        assertEquals("Buffer capacity must cap strictly at 256", 256, buffer.size)
        assertEquals("Dropped count must be exactly 300 - 256 = 44", 44L, buffer.droppedEventsCount)

        val drained = buffer.drain()
        assertEquals(256, drained.size)
        assertEquals("Post-drain size must be 0", 0, buffer.size)

        // Verify oldest 44 were dropped: first element must be sequence 44
        assertEquals(44, drained.first().sequence)
        assertEquals(44, drained.first().payload["seq"])

        // Last element must be sequence 299
        assertEquals(299, drained.last().sequence)
        assertEquals(299, drained.last().payload["seq"])

        // Verify strict contiguous FIFO ordering
        for (idx in drained.indices) {
            assertEquals(44 + idx, drained[idx].sequence)
        }
    }

    @Test
    fun test5_2_RingBufferConcurrentPushDrainStress() {
        val buffer = RingBuffer<Int>(256)
        val producerCount = 8
        val itemsPerProducer = 500 // Total 4,000 items
        val executor = Executors.newFixedThreadPool(producerCount + 2)
        val latch = CountDownLatch(producerCount)

        for (p in 0 until producerCount) {
            executor.submit {
                try {
                    for (i in 0 until itemsPerProducer) {
                        buffer.push(p * 10_000 + i)
                    }
                } finally {
                    latch.countDown()
                }
            }
        }

        assertTrue("Producers must finish in 5s", latch.await(5, TimeUnit.SECONDS))
        executor.shutdown()

        val finalDrained = buffer.drain()
        val totalAccounted = finalDrained.size + buffer.droppedEventsCount
        assertEquals("Total produced must equal drained + dropped", (producerCount * itemsPerProducer).toLong(), totalAccounted)
    }

    @Test
    fun test5_3_RingBufferMemoryLeakFreeOnHeavyDrop() {
        val buffer = RingBuffer<ByteArray>(256)
        // Push 20,000 buffers (1KB each = ~20MB total throughput)
        for (i in 0 until 20_000) {
            buffer.push(ByteArray(1024))
        }

        assertEquals(256, buffer.size)
        assertEquals(20_000L - 256L, buffer.droppedEventsCount)

        // Clear resets everything
        buffer.clear()
        assertEquals(0, buffer.size)
        assertEquals(0L, buffer.droppedEventsCount)
        assertTrue(buffer.drain().isEmpty())
    }
}
