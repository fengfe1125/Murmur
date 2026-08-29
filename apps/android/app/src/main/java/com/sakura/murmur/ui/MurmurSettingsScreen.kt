package com.sakura.murmur.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimePicker
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.sakura.murmur.MurmurConnectionState
import com.sakura.murmur.MurmurDevice
import com.sakura.murmur.MurmurSessionModel
import com.sakura.murmur.NotificationAuthorization
import com.sakura.murmur.NotificationPermission

/**
 * The settings pane — the Android counterpart of `MurmurSettingsView` in
 * `MurmurChatView.swift`: connection state, devices, proactive preferences,
 * notifications, clearing the current moment, and account deletion.
 */
@Composable
fun MurmurSettingsScreen(session: MurmurSessionModel, onClose: () -> Unit) {
    val state by session.uiState.collectAsState()
    val colors = MurmurTheme.colors
    val context = LocalContext.current
    var permissionRefresh by remember { mutableIntStateOf(0) }

    LaunchedEffect(Unit) {
        session.loadPreferences()
        session.refreshDevices()
    }
    // Removing the current device or deleting the account lands back on the
    // enrollment screen; the settings pane closes with it (iOS dismisses too).
    LaunchedEffect(state.identity) {
        if (state.identity == null) onClose()
    }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { permissionRefresh++ }

    // Recomputed whenever a permission request settles, so the label and the
    // "允许通知 / 前往系统设置" switch stay honest.
    val authorization = remember(permissionRefresh) { NotificationPermission.authorization(context) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.paper)
            .safeDrawingPadding()
            .padding(horizontal = MurmurSpacing.xl),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(vertical = MurmurSpacing.sm),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Murmur", style = MaterialTheme.typography.headlineMedium, color = colors.ink)
            Spacer(Modifier.width(MurmurSpacing.md))
            Text("设置", color = colors.secondaryInk, fontSize = 15.sp)
            Spacer(Modifier.weight(1f))
            TextButton(onClick = onClose, modifier = Modifier.height(48.dp)) {
                Text("完成", color = colors.olive)
            }
        }
        HorizontalDivider(color = colors.rule)

        Column(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(MurmurSpacing.lg),
        ) {
            Spacer(Modifier.height(MurmurSpacing.md))

            // ---- 连接 ---------------------------------------------------------
            SettingsSection(title = "连接") {
                SettingsRow(label = "状态", value = state.connection.label)
                if (state.requiresDeviceReconnect) {
                    Text(
                        "本机安全身份已失效。重置只会移除本机绑定，不会删除 Murmur 的记忆。",
                        color = colors.coral,
                        fontSize = 13.sp,
                    )
                    ReconnectButton(session)
                }
                Text(
                    "每个邀请用户最多可绑定 3 台设备。新增设备需要管理员签发设备码。",
                    color = colors.secondaryInk,
                    fontSize = 13.sp,
                )
            }

            // ---- 设备 ---------------------------------------------------------
            SettingsSection(title = "设备") {
                when {
                    !state.devicesLoaded -> Text("正在读取设备", color = colors.secondaryInk, fontSize = 14.sp)
                    state.devices.isEmpty() -> Text("还没有绑定设备", color = colors.secondaryInk, fontSize = 14.sp)
                    else -> state.devices.forEach { device ->
                        DeviceRow(device = device, isCurrent = device.id == state.identity?.deviceID, session = session)
                    }
                }
            }

            // ---- 主动消息 ------------------------------------------------------
            SettingsSection(title = "主动消息") {
                Text("每天最多", color = colors.ink, fontSize = 14.sp)
                Row(horizontalArrangement = Arrangement.spacedBy(MurmurSpacing.sm)) {
                    listOf(0 to "关闭", 2 to "2 条", 3 to "3 条", 4 to "4 条").forEach { (value, label) ->
                        FilterChip(
                            selected = state.preferences.dailyFrequency == value,
                            onClick = {
                                session.updatePreferencesField { it.copy(dailyFrequency = value) }
                            },
                            label = { Text(label) },
                        )
                    }
                }
                TimeRow(
                    label = "安静从",
                    value = state.preferences.quietStart,
                    onChange = { newTime ->
                        session.updatePreferencesField { it.copy(quietStart = newTime) }
                    },
                )
                TimeRow(
                    label = "安静到",
                    value = state.preferences.quietEnd,
                    onChange = { newTime ->
                        session.updatePreferencesField { it.copy(quietEnd = newTime) }
                    },
                )
                Button(
                    onClick = session::savePreferences,
                    modifier = Modifier.height(48.dp),
                    colors = ButtonDefaults.buttonColors(containerColor = colors.olive),
                ) {
                    Text("保存频率与时段", fontSize = 15.sp)
                }
            }

            // ---- 通知 ---------------------------------------------------------
            SettingsSection(title = "通知") {
                SettingsRow(label = "系统通知", value = authorizationLabel(authorization))
                if (authorization == NotificationAuthorization.NotDetermined && Build.VERSION.SDK_INT >= 33) {
                    Button(
                        onClick = { permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS) },
                        modifier = Modifier.height(48.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = colors.olive),
                    ) {
                        Text("允许通知", fontSize = 15.sp)
                    }
                } else if (authorization == NotificationAuthorization.Denied) {
                    Button(
                        onClick = { NotificationPermission.openSystemSettings(context) },
                        modifier = Modifier.height(48.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = colors.olive),
                    ) {
                        Text("前往系统设置", fontSize = 15.sp)
                    }
                }
            }

            // ---- 当前界面 ------------------------------------------------------
            SettingsSection(title = "当前界面") {
                val hasAnything = state.hasCurrentMoment || state.draftPhoto != null || state.draftText.isNotEmpty()
                OutlinedButton(
                    onClick = session::clearCurrent,
                    modifier = Modifier.height(48.dp),
                    enabled = hasAnything,
                ) {
                    Text("清空这一刻", color = colors.ink)
                }
                Text(
                    "App 不会保存聊天列表；冷启动时始终从空白开始。",
                    color = colors.secondaryInk,
                    fontSize = 13.sp,
                )
            }

            // ---- 账号 ---------------------------------------------------------
            SettingsSection(title = "") {
                DeleteAccountButton(session)
                Text(
                    "删除会移除设备身份、服务端记忆、dossier、照片预览和待处理任务，无法撤销。",
                    color = colors.secondaryInk,
                    fontSize = 13.sp,
                )
            }

            state.settingsMessage?.let {
                Text(it, color = colors.secondaryInk, fontSize = 14.sp)
            }
            Spacer(Modifier.height(MurmurSpacing.xl))
        }
    }
}

private fun authorizationLabel(authorization: NotificationAuthorization): String = when (authorization) {
    NotificationAuthorization.Allowed -> "已允许"
    NotificationAuthorization.Denied -> "已关闭"
    NotificationAuthorization.NotDetermined -> "尚未询问"
    NotificationAuthorization.Unknown -> "未知"
}

@Composable
private fun SettingsSection(title: String, content: @Composable () -> Unit) {
    val colors = MurmurTheme.colors
    Column(verticalArrangement = Arrangement.spacedBy(MurmurSpacing.sm)) {
        if (title.isNotEmpty()) {
            Text(title, color = colors.secondaryInk, fontSize = 13.sp)
        }
        content()
    }
}

@Composable
private fun SettingsRow(label: String, value: String) {
    val colors = MurmurTheme.colors
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, color = colors.ink, fontSize = 15.sp)
        Spacer(Modifier.weight(1f))
        Text(value, color = colors.secondaryInk, fontSize = 14.sp)
    }
}

@Composable
private fun DeviceRow(device: MurmurDevice, isCurrent: Boolean, session: MurmurSessionModel) {
    val colors = MurmurTheme.colors
    var confirmRemove by remember { mutableStateOf(false) }
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(device.deviceName ?: "设备", color = colors.ink, fontSize = 15.sp)
                if (isCurrent) {
                    Spacer(Modifier.width(MurmurSpacing.sm))
                    Text("当前", color = colors.olive, fontSize = 13.sp)
                }
            }
            Text(
                if (device.pushEnabled) "推送已连接" else "推送未连接",
                color = colors.secondaryInk,
                fontSize = 13.sp,
            )
        }
        IconButton(
            onClick = { confirmRemove = true },
            modifier = Modifier
                .size(48.dp)
                .testTag("remove-device-${device.id}"),
        ) {
            Icon(Icons.Outlined.Delete, contentDescription = "移除 ${device.deviceName ?: "设备"}", tint = colors.coral)
        }
    }
    if (confirmRemove) {
        MurmurConfirmDialog(
            title = if (isCurrent) "移除当前设备？" else "移除这台设备？",
            message = if (isCurrent) {
                "当前设备会立即退出，需要管理员签发新的设备码才能再次连接。服务端记忆不会因此删除。"
            } else {
                "这台设备之后不能再连接 Murmur。"
            },
            confirmTitle = "确认移除",
            destructive = true,
            onConfirm = {
                confirmRemove = false
                session.removeDevice(device)
            },
            onDismiss = { confirmRemove = false },
        )
    }
}

@Composable
private fun TimeRow(label: String, value: String, onChange: (String) -> Unit) {
    val colors = MurmurTheme.colors
    var showPicker by remember { mutableStateOf(false) }
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, color = colors.ink, fontSize = 15.sp)
        Spacer(Modifier.weight(1f))
        TextButton(onClick = { showPicker = true }, modifier = Modifier.height(48.dp)) {
            Text(value, color = colors.olive, fontSize = 15.sp)
        }
    }
    if (showPicker) {
        TimePickerDialog(
            title = label,
            initial = value,
            onConfirm = {
                onChange(it)
                showPicker = false
            },
            onDismiss = { showPicker = false },
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun TimePickerDialog(
    title: String,
    initial: String,
    onConfirm: (String) -> Unit,
    onDismiss: () -> Unit,
) {
    val parts = initial.split(":")
    val timeState = rememberTimePickerState(
        initialHour = parts.getOrNull(0)?.toIntOrNull() ?: 8,
        initialMinute = parts.getOrNull(1)?.toIntOrNull() ?: 0,
        is24Hour = true,
    )
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { TimePicker(state = timeState) },
        confirmButton = {
            TextButton(onClick = { onConfirm("%02d:%02d".format(timeState.hour, timeState.minute)) }) {
                Text("确定")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消") } },
    )
}

@Composable
private fun ReconnectButton(session: MurmurSessionModel) {
    var confirmReset by remember { mutableStateOf(false) }
    Button(
        onClick = { confirmReset = true },
        modifier = Modifier.height(48.dp),
        colors = ButtonDefaults.buttonColors(containerColor = MurmurTheme.colors.coral),
    ) {
        Text("重新连接此设备", fontSize = 15.sp)
    }
    if (confirmReset) {
        MurmurConfirmDialog(
            title = "重置本机安全身份？",
            message = "重置后需要管理员签发新的设备码才能再次连接；账号与服务端记忆不会被删除。",
            confirmTitle = "确认重置",
            destructive = true,
            onConfirm = {
                confirmReset = false
                session.resetLocalDeviceIdentity()
            },
            onDismiss = { confirmReset = false },
        )
    }
}

@Composable
private fun DeleteAccountButton(session: MurmurSessionModel) {
    val colors = MurmurTheme.colors
    var confirmDelete by remember { mutableStateOf(false) }
    Button(
        onClick = { confirmDelete = true },
        modifier = Modifier.height(48.dp),
        colors = ButtonDefaults.buttonColors(containerColor = colors.coral),
    ) {
        Text("删除账号与全部记忆", fontSize = 15.sp)
    }
    if (confirmDelete) {
        MurmurConfirmDialog(
            title = "删除账号与全部记忆？",
            message = "这个操作无法撤销。",
            confirmTitle = "确认删除",
            destructive = true,
            onConfirm = {
                confirmDelete = false
                session.deleteAccount()
            },
            onDismiss = { confirmDelete = false },
        )
    }
}

@Composable
fun MurmurConfirmDialog(
    title: String,
    message: String,
    confirmTitle: String,
    destructive: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    val colors = MurmurTheme.colors
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title, color = colors.ink) },
        text = { Text(message, color = colors.secondaryInk) },
        confirmButton = {
            TextButton(onClick = onConfirm, modifier = Modifier.testTag("confirm-dialog-confirm")) {
                Text(confirmTitle, color = if (destructive) colors.coral else colors.olive)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("取消", color = colors.secondaryInk) } },
    )
}
