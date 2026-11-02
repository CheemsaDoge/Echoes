package dev.echoledger.app.data

import android.content.Context
import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Entity(tableName = "sessions")
data class LocalSession(
    @PrimaryKey val id: String,
    val startedAtMs: Long,
    val timezoneOffsetMin: Int,
    val purpose: String,
    val state: String = "recording",
    val finalSeq: Int? = null,
    val remoteCreated: Boolean = false,
    val remoteClosed: Boolean = false,
)

@Entity(tableName = "chunks", indices = [Index(value = ["sessionId", "seq"], unique = true)])
data class LocalChunk(
    @PrimaryKey val id: String,
    val sessionId: String,
    val seq: Int,
    val startSample: Long,
    val sampleCount: Int,
    val path: String,
    val sha256: String,
    val startMonotonicNs: Long,
    val clockDriftMs: Long,
    val uploadState: String = "pending",
    val error: String = "",
)

@Entity(tableName = "cache", primaryKeys = ["kind", "id"])
data class CacheEntry(val kind: String, val id: String, val sortMs: Long = 0, val endMs: Long = 0, val payload: String)

@Dao
abstract class JournalDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE) abstract suspend fun addSession(session: LocalSession)
    @Insert(onConflict = OnConflictStrategy.IGNORE) abstract suspend fun addChunk(chunk: LocalChunk)
    @Query("SELECT * FROM sessions WHERE id=:id") abstract suspend fun session(id: String): LocalSession?
    @Query("SELECT * FROM sessions WHERE state != 'deleted' ORDER BY startedAtMs") abstract suspend fun sessions(): List<LocalSession>
    @Query("SELECT * FROM sessions WHERE state != 'deleted' ORDER BY startedAtMs DESC") abstract fun observeSessions(): Flow<List<LocalSession>>
    @Query("UPDATE sessions SET state=:state,finalSeq=:seq WHERE id=:id") abstract suspend fun finishSession(id: String, seq: Int, state: String)
    @Query("UPDATE sessions SET remoteCreated=1 WHERE id=:id") abstract suspend fun markCreated(id: String)
    @Query("UPDATE sessions SET remoteClosed=1 WHERE id=:id") abstract suspend fun markClosed(id: String)
    @Query("UPDATE sessions SET state='deleted' WHERE id=:id") abstract suspend fun tombstone(id: String)
    @Query("SELECT * FROM chunks WHERE id=:id") abstract suspend fun chunk(id: String): LocalChunk?
    @Query("SELECT * FROM chunks WHERE sessionId=:id ORDER BY seq") abstract suspend fun chunks(id: String): List<LocalChunk>
    @Query("SELECT * FROM chunks WHERE sessionId=:id AND uploadState='pending' ORDER BY seq") abstract suspend fun pending(id: String): List<LocalChunk>
    @Query("SELECT * FROM chunks WHERE sessionId IN (SELECT id FROM sessions WHERE purpose='calibration') ORDER BY rowid DESC LIMIT 1")
    abstract suspend fun latestCalibration(): LocalChunk?
    @Query("SELECT COUNT(*) FROM chunks WHERE uploadState='pending' AND sessionId IN (SELECT id FROM sessions WHERE state!='deleted')")
    abstract fun pendingCount(): Flow<Int>
    @Query("UPDATE chunks SET uploadState=:state,error=:error WHERE id=:id") abstract suspend fun markUpload(id: String, state: String, error: String = "")
    @Query("UPDATE sessions SET remoteCreated=0,remoteClosed=0 WHERE state!='deleted'") abstract suspend fun resetSessionsRemote()
    @Query("UPDATE chunks SET uploadState='pending',error='' WHERE sessionId IN (SELECT id FROM sessions WHERE state!='deleted')") abstract suspend fun resetChunksRemote()
    @Query("SELECT * FROM cache WHERE kind=:kind ORDER BY sortMs DESC") abstract fun observe(kind: String): Flow<List<CacheEntry>>
    @Insert(onConflict = OnConflictStrategy.REPLACE) abstract suspend fun putCache(entries: List<CacheEntry>)
    @Query("DELETE FROM cache WHERE kind=:kind") abstract suspend fun clearKind(kind: String)
    @Query("DELETE FROM cache WHERE kind='events' AND endMs>=:from AND sortMs<:to") abstract suspend fun clearEventsRange(from: Long, to: Long)
    @Query("DELETE FROM cache") abstract suspend fun clearCache()
    @Query("DELETE FROM chunks WHERE sessionId=:id") abstract suspend fun deleteChunks(id: String)
    @Query("DELETE FROM sessions WHERE id=:id") abstract suspend fun deleteSession(id: String)
    @Transaction open suspend fun replace(kind: String, entries: List<CacheEntry>) { clearKind(kind); putCache(entries) }
    @Transaction open suspend fun replaceEvents(from: Long, to: Long, entries: List<CacheEntry>) { clearEventsRange(from, to); putCache(entries) }
    @Transaction open suspend fun resetRemote() { resetSessionsRemote(); resetChunksRemote(); clearCache() }
    @Transaction open suspend fun deleteLocal(id: String) { deleteChunks(id); deleteSession(id) }
}

@Database(entities = [LocalSession::class, LocalChunk::class, CacheEntry::class], version = 1, exportSchema = true)
abstract class JournalDatabase : RoomDatabase() {
    abstract fun dao(): JournalDao
    companion object {
        fun create(context: Context): JournalDatabase = Room.databaseBuilder(context, JournalDatabase::class.java, "journal.db").build()
    }
}
