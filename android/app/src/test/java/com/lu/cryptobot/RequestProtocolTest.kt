package com.lu.cryptobot
import org.junit.Assert.*
import org.junit.Test
class RequestProtocolTest {
    @Test fun emptyBodyHashMatchesServer() { assertEquals("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",RequestProtocol.hash(byteArrayOf())) }
    @Test fun queryIsBoundToSignature() { assertNotEquals(RequestProtocol.canonical("GET","/x?a=1",byteArrayOf(),"1","n","r"),RequestProtocol.canonical("GET","/x?a=2",byteArrayOf(),"1","n","r")) }
    @Test fun unreachableNeverShowsCachedOn() { assertEquals("UNKNOWN",RequestProtocol.authoritativeState("LIVE_ON",false)) }
    @Test fun hardLockRetained() { assertEquals("HARD_LOCKED",RequestProtocol.authoritativeState("HARD_LOCKED",true)) }
    @Test fun malformedStateIsUnknown() { assertEquals("UNKNOWN",RequestProtocol.authoritativeState("ON",true)) }
}
