package dev.echoledger.app

import android.app.Application
import dev.echoledger.app.data.*
import dev.echoledger.app.network.UploadScheduler
import kotlinx.coroutines.*

class EchoApp : Application() {
    lateinit var graph: AppGraph
        private set
    override fun onCreate() { super.onCreate(); graph = AppGraph(this) }
}
class AppGraph(val app: Application) {
    val database = JournalDatabase.create(app)
    val prefs = SecurePreferences(app)
    val local = LocalStore(app, database.dao())
    val repository = Repository(app, database.dao(), prefs, local)
    val ready = CompletableDeferred<Unit>()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    init {
        scope.launch {
            try { local.recover() }
            catch (e: Exception) { repository.message.value = "Local recovery requires attention: ${e.javaClass.simpleName}" }
            finally { ready.complete(Unit) }
            UploadScheduler.scheduleRecovery(app)
            UploadScheduler.enqueue(app)
            dev.echoledger.app.network.StandaloneScheduler.scheduleRecovery(app)
            dev.echoledger.app.network.StandaloneScheduler.enqueue(app)
        }
    }
}
