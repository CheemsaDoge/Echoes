package net.hemory.app.native

import org.junit.Assert.assertEquals
import org.junit.Test

class DeadManSwitchDiscriminatorTest {

    @Test
    fun testHalDeadlockDetectedWhenStaleOver45s() {
        val action = WatchdogDiscriminator.evaluate(
            isServiceRunning = true,
            frameStalenessMs = 46_000L
        )
        assertEquals(WatchdogAction.HAL_DEADLOCK_RESTART, action)
    }

    @Test
    fun testFalseAlarmRearmWhenAudioActiveWithin45s() {
        val action = WatchdogDiscriminator.evaluate(
            isServiceRunning = true,
            frameStalenessMs = 30_000L
        )
        assertEquals(WatchdogAction.FALSE_ALARM_REARM, action)
    }

    @Test
    fun testServiceDeadPostsAlert() {
        val action = WatchdogDiscriminator.evaluate(
            isServiceRunning = false,
            frameStalenessMs = 10_000L
        )
        assertEquals(WatchdogAction.SERVICE_DEAD_ALERT, action)
    }

    @Test
    fun testExact45SecondsBoundaryEvaluatesFalseAlarmRearm() {
        val action = WatchdogDiscriminator.evaluate(
            isServiceRunning = true,
            frameStalenessMs = 45_000L
        )
        assertEquals(WatchdogAction.FALSE_ALARM_REARM, action)
    }

    @Test
    fun testOneMillisecondOver45SecondsTriggersHalDeadlock() {
        val action = WatchdogDiscriminator.evaluate(
            isServiceRunning = true,
            frameStalenessMs = 45_001L
        )
        assertEquals(WatchdogAction.HAL_DEADLOCK_RESTART, action)
    }
}
