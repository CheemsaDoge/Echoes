package net.hemory.app.native

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import net.hemory.app.MainActivity

enum class WatchdogAction {
    HAL_DEADLOCK_RESTART,
    FALSE_ALARM_REARM,
    SERVICE_DEAD_ALERT
}

object WatchdogDiscriminator {
    fun evaluate(
        isServiceRunning: Boolean,
        frameStalenessMs: Long,
        staleThresholdMs: Long = DeadManSwitchAlarmReceiver.AUDIO_FRAME_STALE_THRESHOLD_MS
    ): WatchdogAction {
        return when {
            isServiceRunning && frameStalenessMs > staleThresholdMs -> WatchdogAction.HAL_DEADLOCK_RESTART
            isServiceRunning && frameStalenessMs <= staleThresholdMs -> WatchdogAction.FALSE_ALARM_REARM
            else -> WatchdogAction.SERVICE_DEAD_ALERT
        }
    }
}

class DeadManSwitchAlarmReceiver : BroadcastReceiver() {
    companion object {
        const val TAG = "DeadManSwitchReceiver"
        const val ACTION_FIRE = "net.hemory.app.DEAD_MAN_SWITCH_FIRE"
        const val CHANNEL_ID = "hemory_recording_alerts"
        const val NOTIFICATION_ID = 1214606592
        const val AUDIO_FRAME_STALE_THRESHOLD_MS = 45_000L // 45s stall threshold
    }

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != ACTION_FIRE) return

        val recordingId = intent.getStringExtra("recording_id") ?: "unknown"
        val elapsedMs = intent.getLongExtra("elapsed_ms", 0L)
        Log.w(TAG, "Dead-man switch alarm fired [rid=$recordingId, elapsed=${elapsedMs}ms]")

        val isServiceRunning = RecordingForegroundService.isServiceRunning
        val lastFrameMonoMs = RecordingForegroundService.lastAudioFrameTimestampMs
        val nowMono = SystemClock.elapsedRealtime()
        val frameStalenessMs = nowMono - lastFrameMonoMs

        val action = WatchdogDiscriminator.evaluate(
            isServiceRunning = isServiceRunning,
            frameStalenessMs = frameStalenessMs,
            staleThresholdMs = AUDIO_FRAME_STALE_THRESHOLD_MS
        )

        when (action) {
            WatchdogAction.HAL_DEADLOCK_RESTART -> {
                Log.e(TAG, "Audio HAL 读阻塞/音频流冻结 (staleness=${frameStalenessMs}ms > 45s)，触发自愈 [rid=$recordingId]")
                SelfHealingController.restartRecording(context, recordingId, "AUDIO_HAL_FREEZE")
            }
            WatchdogAction.FALSE_ALARM_REARM -> {
                Log.w(TAG, "前台录音与音频帧正常活跃 (staleness=${frameStalenessMs}ms)，判定为心跳延迟误报 [rid=$recordingId]")
                val scheduler = AlarmSchedulerHelper(context)
                scheduler.scheduleHeartbeat(recordingId, elapsedMs, 60L)
            }
            WatchdogAction.SERVICE_DEAD_ALERT -> {
                Log.e(TAG, "前台服务已终止，投递高优先级告警通知 [rid=$recordingId]")
                postHeadsUpNotification(context, recordingId, elapsedMs)
            }
        }
    }

    private fun postHeadsUpNotification(context: Context, recordingId: String, elapsedMs: Long) {
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Recording Critical Alerts",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Alerts when continuous background recording is interrupted"
                enableVibration(true)
            }
            nm.createNotificationChannel(channel)
        }

        val recoveryIntent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra("hemory.listening.action.source", "deadSwitchNotification")
            putExtra("hemory.listening.action.createdAtMs", System.currentTimeMillis())
            putExtra("recording_id", recordingId)
        }
        val pi = PendingIntent.getActivity(
            context,
            NOTIFICATION_ID,
            recoveryIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val minutes = maxOf(0L, elapsedMs / 60000L)
        val bodyText = if (minutes < 1) "录音服务已中断，点击立即恢复" else "录音服务在运行 ${minutes} 分钟后中断，点击立即恢复"

        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_dialog_alert)
            .setContentTitle("录音意外中断")
            .setContentText(bodyText)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build()

        try {
            nm.notify(NOTIFICATION_ID, notification)
        } catch (e: SecurityException) {
            Log.e(TAG, "Notification permission missing; failed to post dead-man notification", e)
        }
    }
}

/** Controller managing recovery restarts upon Audio HAL deadlock */
object SelfHealingController {
    private const val TAG = "SelfHealingController"

    fun restartRecording(context: Context, deadRecordingId: String, reason: String) {
        Log.i(TAG, "Initiating self-healing recovery: deadRid=$deadRecordingId, reason=$reason")
        // Finalize broken manifest
        runCatching {
            SegmentManifestManager.get(context, deadRecordingId).finalizeSession("HAL_LOCKUP")
        }
        // Restart foreground service explicitly with ACTION_RECOVER
        val serviceIntent = Intent(context, RecordingForegroundService::class.java).apply {
            action = RecordingForegroundService.ACTION_RECOVER
            putExtra(RecordingForegroundService.EXTRA_RECORDING_ID, "rec_recovered_${System.currentTimeMillis()}")
            putExtra(RecordingForegroundService.EXTRA_TENANT_ID, RecordingForegroundService.activeTenantId ?: "")
            putExtra(RecordingForegroundService.EXTRA_VAULT_ID, RecordingForegroundService.activeVaultId ?: "")
            putExtra("recovery_reason", reason)
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            context.startForegroundService(serviceIntent)
        } else {
            context.startService(serviceIntent)
        }
    }
}
