package net.hemory.app.native

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.SystemClock
import android.util.Log

class AlarmSchedulerHelper(private val context: Context) {
    companion object {
        const val TAG = "AlarmSchedulerHelper"
        const val ACTION_DEAD_MAN_FIRE = "net.hemory.app.DEAD_MAN_SWITCH_FIRE"
        const val PENDING_INTENT_FLAGS = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
    }

    private val alarmManager = context.getSystemService(Context.ALARM_SERVICE) as AlarmManager

    fun scheduleHeartbeat(recordingId: String, elapsedMs: Long, leaseSeconds: Long) {
        val pi = createPendingIntent(recordingId, elapsedMs)
        // Cancel existing lease
        alarmManager.cancel(pi)

        val triggerAtRealtime = SystemClock.elapsedRealtime() + (leaseSeconds * 1000L)
        val canScheduleExact = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            alarmManager.canScheduleExactAlarms()
        } else {
            true
        }

        try {
            if (canScheduleExact) {
                alarmManager.setExactAndAllowWhileIdle(
                    AlarmManager.ELAPSED_REALTIME_WAKEUP,
                    triggerAtRealtime,
                    pi
                )
            } else {
                alarmManager.setAndAllowWhileIdle(
                    AlarmManager.ELAPSED_REALTIME_WAKEUP,
                    triggerAtRealtime,
                    pi
                )
            }
            Log.d(TAG, "scheduleHeartbeat: armed lease=${leaseSeconds}s exact=$canScheduleExact [rid=$recordingId]")
        } catch (e: SecurityException) {
            Log.w(TAG, "exact alarm rejected, degrading to allow-while-idle [rid=$recordingId]", e)
            alarmManager.setAndAllowWhileIdle(
                AlarmManager.ELAPSED_REALTIME_WAKEUP,
                triggerAtRealtime,
                pi
            )
        }
    }

    fun cancelHeartbeat(recordingId: String) {
        val pi = createPendingIntent(recordingId, 0L)
        alarmManager.cancel(pi)
        // Dismiss alert notification if showing
        val notificationManager = context.getSystemService(Context.NOTIFICATION_SERVICE) as android.app.NotificationManager
        notificationManager.cancel(DeadManSwitchAlarmReceiver.NOTIFICATION_ID)
        Log.d(TAG, "cancelHeartbeat [rid=$recordingId]")
    }

    private fun createPendingIntent(recordingId: String, elapsedMs: Long): PendingIntent {
        val intent = Intent(context, DeadManSwitchAlarmReceiver::class.java).apply {
            action = ACTION_DEAD_MAN_FIRE
            putExtra("recording_id", recordingId)
            putExtra("elapsed_ms", elapsedMs)
        }
        return PendingIntent.getBroadcast(context, 0, intent, PENDING_INTENT_FLAGS)
    }
}
