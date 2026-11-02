package dev.echoledger.app

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.lifecycle.ViewModelProvider
import dev.echoledger.app.capture.RecordingService
import dev.echoledger.app.ui.AppScreen
import dev.echoledger.app.ui.AppViewModel

class MainActivity : ComponentActivity() {
    private lateinit var vm: AppViewModel
    private var pendingCalibration = false
    private val permissions = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) startCapture(pendingCalibration)
        else if (::vm.isInitialized) vm.repo.message.value = "Microphone permission was not granted"
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        vm = ViewModelProvider(this)[AppViewModel::class.java]
        setContent {
            AppScreen(vm,
                onRecord = { calibration ->
                    pendingCalibration = calibration
                    val requested = mutableListOf(Manifest.permission.RECORD_AUDIO)
                    if (Build.VERSION.SDK_INT >= 33) requested.add(Manifest.permission.POST_NOTIFICATIONS)
                    if (requested.any { ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED }) permissions.launch(requested.toTypedArray())
                    else startCapture(calibration)
                },
                onStop = { startService(Intent(this, RecordingService::class.java).setAction(RecordingService.STOP)) })
        }
    }
    private fun startCapture(calibration: Boolean) {
        try { ContextCompat.startForegroundService(this, Intent(this, RecordingService::class.java).apply {
            if (calibration) action = RecordingService.CALIBRATE
        }) } catch (e: Exception) { vm.repo.message.value = "Recording could not start: ${e.javaClass.simpleName}" }
    }
    override fun onStart() { super.onStart(); if (::vm.isInitialized) vm.connect() }
    override fun onStop() { if (::vm.isInitialized) vm.disconnect(); super.onStop() }
}
