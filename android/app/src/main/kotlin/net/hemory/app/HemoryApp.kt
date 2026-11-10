package net.hemory.app

import android.app.Application
import net.hemory.app.worker.UploadScheduler

class HemoryApp : Application() {
    override fun onCreate() {
        super.onCreate()
        runCatching {
            UploadScheduler.schedulePeriodicRecovery(this)
        }
    }
}
