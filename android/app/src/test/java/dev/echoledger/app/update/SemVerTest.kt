package dev.echoledger.app.update

import org.junit.Assert.*
import org.junit.Test

class SemVerTest {

    @Test
    fun testParseValidStandardSemVer() {
        val v1 = SemVer.parse("0.1.0")
        assertNotNull("0.1.0 must parse cleanly", v1)
        assertEquals(0, v1!!.major)
        assertEquals(1, v1.minor)
        assertEquals(0, v1.patch)
        assertNull(v1.preRelease)

        val v2 = SemVer.parse("1.2.3")
        assertNotNull("1.2.3 must parse cleanly", v2)
        assertEquals(1, v2!!.major)
        assertEquals(2, v2.minor)
        assertEquals(3, v2.patch)
        assertNull(v2.preRelease)
    }

    @Test
    fun testParseWithLeadingV() {
        val lowerV = SemVer.parse("v0.2.0")
        assertNotNull("v0.2.0 must parse", lowerV)
        assertEquals(SemVer(0, 2, 0), lowerV)

        val upperV = SemVer.parse("V1.0.0")
        assertNotNull("V1.0.0 must parse", upperV)
        assertEquals(SemVer(1, 0, 0), upperV)

        val spacedV = SemVer.parse("  v2.5.9  ")
        assertNotNull("Padded v2.5.9 must parse", spacedV)
        assertEquals(SemVer(2, 5, 9), spacedV)
    }

    @Test
    fun testParseEqualityIgnoringVPrefix() {
        val vPrefix = SemVer.parse("v0.1.0")
        val noPrefix = SemVer.parse("0.1.0")
        val capVPrefix = SemVer.parse("V0.1.0")

        assertEquals(vPrefix, noPrefix)
        assertEquals(capVPrefix, noPrefix)
        assertEquals(vPrefix.hashCode(), noPrefix.hashCode())
    }

    @Test
    fun testParseWithPreReleaseAndBuildMetadata() {
        val pre = SemVer.parse("1.0.0-alpha")
        assertNotNull(pre)
        assertEquals(1, pre!!.major)
        assertEquals(0, pre.minor)
        assertEquals(0, pre.patch)
        assertEquals("alpha", pre.preRelease)

        val preWithDots = SemVer.parse("v2.1.0-beta.1")
        assertNotNull(preWithDots)
        assertEquals(SemVer(2, 1, 0, "beta.1"), preWithDots)

        val withBuildMeta = SemVer.parse("1.2.3-rc.2+20261103.sha123")
        assertNotNull(withBuildMeta)
        assertEquals(SemVer(1, 2, 3, "rc.2"), withBuildMeta)
    }

    @Test
    fun testVersionOrdering() {
        val v010 = SemVer.parse("0.1.0")!!
        val v020 = SemVer.parse("0.2.0")!!
        val v100 = SemVer.parse("1.0.0")!!
        val v100Alpha = SemVer.parse("1.0.0-alpha")!!
        val v100Beta = SemVer.parse("1.0.0-beta")!!
        val v200 = SemVer.parse("2.0.0")!!

        // Minor version ordering
        assertTrue("0.1.0 must be less than 0.2.0", v010 < v020)
        assertTrue("0.2.0 must be greater than 0.1.0", v020 > v010)

        // Major version ordering
        assertTrue("0.2.0 must be less than 1.0.0", v020 < v100)
        assertTrue("1.0.0 must be less than 2.0.0", v100 < v200)

        // Patch version ordering
        val v101 = SemVer.parse("1.0.1")!!
        assertTrue("1.0.0 must be less than 1.0.1", v100 < v101)

        // Pre-release precedence: normal release > pre-release (SemVer 2.0.0 rule)
        assertTrue("1.0.0-alpha must be less than 1.0.0", v100Alpha < v100)
        assertTrue("1.0.0-alpha must be less than 1.0.0-beta", v100Alpha < v100Beta)
        assertTrue("1.0.0-beta must be less than 1.0.0", v100Beta < v100)

        // Numeric prerelease precedence: 1.0.0-rc.2 < 1.0.0-rc.10
        val rc2 = SemVer.parse("1.0.0-rc.2")!!
        val rc10 = SemVer.parse("1.0.0-rc.10")!!
        assertTrue("1.0.0-rc.2 must be less than 1.0.0-rc.10", rc2 < rc10)

        val alpha1 = SemVer.parse("1.0.0-alpha.1")!!
        val alphaBeta = SemVer.parse("1.0.0-alpha.beta")!!
        assertTrue("1.0.0-alpha must be less than 1.0.0-alpha.1", v100Alpha < alpha1)
        assertTrue("1.0.0-alpha.1 must be less than 1.0.0-alpha.beta", alpha1 < alphaBeta)

        // Hyphenated non-numeric identifiers must use ASCII ordering (1.0.0--1 < 1.0.0--2)
        val hyphen1 = SemVer.parse("1.0.0--1")!!
        val hyphen2 = SemVer.parse("1.0.0--2")!!
        assertTrue("1.0.0--1 must precede 1.0.0--2 in ASCII order", hyphen1 < hyphen2)

        // Equality comparison
        val v010Duplicate = SemVer.parse("0.1.0")!!
        assertEquals(0, v010.compareTo(v010Duplicate))
    }

    @Test
    fun testParseInvalidVersionsReturnNullSafely() {
        val invalidInputs = listOf(
            "",
            "   ",
            "v",
            "V",
            "abc",
            "not.a.version",
            "1.2",
            "1",
            "1.2.3.4",
            "1.a.3",
            "a.1.2",
            "1.2.c",
            "-1.0.0",
            "1.-2.0",
            "1.0.-3",
            "v.",
            "1.2.",
            ".1.2",
            "99999999999999999999999999999999.0.0"
        )

        for (invalid in invalidInputs) {
            val parsed = SemVer.parse(invalid)
            assertNull("Input '$invalid' must return null safely", parsed)
        }
    }

    @Test
    fun testToStringFormat() {
        assertEquals("0.1.0", SemVer(0, 1, 0).toString())
        assertEquals("1.2.3-alpha", SemVer(1, 2, 3, "alpha").toString())
    }

    @Test
    fun testBuildConfigVersionIsUpdatedAndMonotonic() {
        val currentSemVer = SemVer.parse(dev.echoledger.app.BuildConfig.VERSION_NAME)
        assertNotNull("BuildConfig.VERSION_NAME (${dev.echoledger.app.BuildConfig.VERSION_NAME}) must be valid SemVer", currentSemVer)
        assertEquals(0, currentSemVer!!.major)
        assertEquals(3, currentSemVer.minor)
        assertTrue(
            "Patch version (${currentSemVer.patch}) must be >= 1",
            currentSemVer.patch >= 1
        )
        assertTrue(
            "BuildConfig.VERSION_NAME ($currentSemVer) must be strictly greater than 0.3.0",
            currentSemVer > SemVer(0, 3, 0)
        )
        assertTrue(
            "BuildConfig.VERSION_CODE (${dev.echoledger.app.BuildConfig.VERSION_CODE}) must be > 1",
            dev.echoledger.app.BuildConfig.VERSION_CODE > 1
        )
    }
}
