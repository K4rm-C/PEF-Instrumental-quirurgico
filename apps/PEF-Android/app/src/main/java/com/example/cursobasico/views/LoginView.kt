package com.example.cursobasico.views

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CenterFocusStrong
import androidx.compose.material.icons.filled.Email
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.cursobasico.ActionColor
import com.example.cursobasico.BorderColor
import com.example.cursobasico.FieldBackground
import com.example.cursobasico.OnActionColor
import com.example.cursobasico.R
import com.example.cursobasico.SurfaceColor
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.TextSecondary
import com.example.cursobasico.auth.LogoutReason
import com.example.cursobasico.ui.theme.LocalPefColors
import com.example.cursobasico.ui.viewmodel.LoginViewModel
import com.example.cursobasico.ui.viewmodel.pefViewModel
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.getValue
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.lifecycle.compose.collectAsStateWithLifecycle

@Composable
fun LoginScreen(
    logoutReason: LogoutReason? = null,
    viewModel: LoginViewModel = pefViewModel { LoginViewModel(it.sessionManager) }
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val focusManager = LocalFocusManager.current
    val reasonMessage = when (logoutReason) {
        is LogoutReason.SessionExpired -> stringResource(R.string.login_session_expired)
        is LogoutReason.UserInitiated -> if (logoutReason.serverConfirmed) {
            stringResource(R.string.login_signed_out)
        } else stringResource(R.string.login_signed_out_local_only)
        null -> null
    }
    Scaffold(
        modifier = Modifier.fillMaxSize(),
        containerColor = SurfaceColor
    ) { paddingValues ->

        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
                .imePadding(),
            contentAlignment = Alignment.Center
        ) {
            Column(
                modifier = Modifier
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp),
                horizontalAlignment = Alignment.CenterHorizontally
            ) {
                Card(
                    modifier = Modifier
                        .fillMaxWidth()
                        .widthIn(max = 390.dp),
                    shape = RoundedCornerShape(12.dp),
                    colors = CardDefaults.cardColors(
                        containerColor = SurfaceColor
                    ),
                    border = BorderStroke(
                        1.dp,
                        BorderColor
                    ),
                    elevation = CardDefaults.cardElevation(
                        defaultElevation = 2.dp
                    )
                ) {
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(
                                horizontal = 26.dp,
                                vertical = 30.dp
                            ),
                        horizontalAlignment = Alignment.CenterHorizontally
                    ) {
                        LoginLogo()
                        Spacer(modifier = Modifier.height(15.dp))
                        Text(
                            text = stringResource(R.string.login_title),
                            fontSize = 17.sp,
                            lineHeight = 21.sp,
                            fontWeight = FontWeight.Bold,
                            color = TextPrimary,
                            textAlign = TextAlign.Center
                        )
                        Spacer(modifier = Modifier.height(5.dp))
                        Text(
                            text = stringResource(R.string.topbar_subtitle),
                            fontSize = 12.sp,
                            color = TextSecondary
                        )
                        Spacer(modifier = Modifier.height(25.dp))
                        HorizontalDivider(
                            color = BorderColor
                        )
                        Spacer(modifier = Modifier.height(23.dp))
                        if (reasonMessage != null && state.errorRes == null) {
                            Text(
                                text = reasonMessage,
                                fontSize = 11.sp,
                                color = TextSecondary,
                                textAlign = TextAlign.Center,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .semantics { liveRegion = LiveRegionMode.Polite }
                            )
                            Spacer(modifier = Modifier.height(14.dp))
                        }
                        LoginTextField(
                            label = stringResource(R.string.login_email_label),
                            value = state.email,
                            onValueChange = viewModel::onEmailChange,
                            placeholder = stringResource(R.string.login_email_placeholder),
                            icon = Icons.Default.Email,
                            enabled = !state.loading
                        )
                        Spacer(modifier = Modifier.height(16.dp))
                        LoginTextField(
                            label = stringResource(R.string.login_password_label),
                            value = state.password,
                            onValueChange = viewModel::onPasswordChange,
                            placeholder = stringResource(R.string.login_password_placeholder),
                            icon = Icons.Default.Lock,
                            isPassword = true,
                            enabled = !state.loading,
                            onDone = {
                                focusManager.clearFocus()
                                viewModel.submit()
                            }
                        )
                        state.errorRes?.let { errorRes ->
                            Spacer(modifier = Modifier.height(12.dp))
                            Text(
                                text = stringResource(errorRes),
                                fontSize = 11.sp,
                                color = LocalPefColors.current.red,
                                textAlign = TextAlign.Center,
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .semantics { liveRegion = LiveRegionMode.Assertive }
                            )
                            state.errorDetail?.let {
                                Text(
                                    text = stringResource(R.string.error_reference, it),
                                    fontSize = 9.sp,
                                    color = TextSecondary,
                                    textAlign = TextAlign.Center,
                                    modifier = Modifier.fillMaxWidth()
                                )
                            }
                        }
                        Spacer(modifier = Modifier.height(23.dp))
                        Button(
                            onClick = {
                                focusManager.clearFocus()
                                viewModel.submit()
                            },
                            enabled = !state.loading,
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(44.dp),
                            shape = RoundedCornerShape(6.dp),
                            colors = ButtonDefaults.buttonColors(
                                containerColor = ActionColor,
                                contentColor = OnActionColor,
                                disabledContainerColor = ActionColor.copy(alpha = 0.6f),
                                disabledContentColor = OnActionColor
                            )
                        ) {
                            if (state.loading) {
                                CircularProgressIndicator(
                                    color = OnActionColor,
                                    strokeWidth = 2.dp,
                                    modifier = Modifier.size(16.dp)
                                )
                                Spacer(modifier = Modifier.size(8.dp))
                            }
                            Text(
                                text = stringResource(
                                    if (state.loading) R.string.login_signing_in else R.string.login_sign_in
                                ),
                                fontSize = 13.sp,
                                fontWeight = FontWeight.Medium
                            )
                        }
                        Spacer(modifier = Modifier.height(22.dp))
                        HorizontalDivider(color = BorderColor)
                        Spacer(modifier = Modifier.height(17.dp))
                        Text(
                            text = stringResource(R.string.login_restricted),
                            fontSize = 10.sp,
                            color = TextSecondary,
                            textAlign = TextAlign.Center
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun LoginLogo() {
    Box(
        modifier = Modifier
            .size(46.dp)
            .clip(RoundedCornerShape(9.dp))
            .background(LocalPefColors.current.logoBackground)
            .border(
                width = 1.dp,
                color = LocalPefColors.current.logoBorder,
                shape = RoundedCornerShape(9.dp)
            ),
        contentAlignment = Alignment.Center
    ) {
        Icon(
            imageVector = Icons.Default.CenterFocusStrong,
            contentDescription = stringResource(R.string.cd_logo),
            modifier = Modifier.size(27.dp),
            tint = LocalPefColors.current.textPrimary
        )
    }
}

@Composable
fun LoginTextField(
    label: String,
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    icon: ImageVector,
    isPassword: Boolean = false,
    enabled: Boolean = true,
    onDone: () -> Unit = {}
) {
    Column(
        modifier = Modifier.fillMaxWidth()
    ) {
        Text(
            text = label,
            fontSize = 11.sp,
            fontWeight = FontWeight.SemiBold,
            color = TextPrimary
        )
        Spacer(modifier = Modifier.height(7.dp))
        OutlinedTextField(
            value = value,
            onValueChange = onValueChange,
            enabled = enabled,
            modifier = Modifier
                .fillMaxWidth()
                .height(52.dp),
            singleLine = true,
            keyboardActions = KeyboardActions(onDone = { onDone() }),
            textStyle = TextStyle(
                fontSize = 12.sp,
                color = TextPrimary
            ),
            placeholder = {
                Text(
                    text = placeholder,
                    fontSize = 11.sp,
                    color = TextSecondary
                )
            },
            leadingIcon = {
                Icon(
                    imageVector = icon,
                    contentDescription = null,
                    modifier = Modifier.size(17.dp),
                    tint = TextSecondary
                )
            },
            visualTransformation = if (isPassword) {
                PasswordVisualTransformation()
            } else {
                VisualTransformation.None
            },
            keyboardOptions = KeyboardOptions(
                keyboardType = if (isPassword) {
                    KeyboardType.Password
                } else {
                    KeyboardType.Email
                },
                imeAction = if (isPassword) {
                    ImeAction.Done
                } else {
                    ImeAction.Next
                }
            ),
            shape = RoundedCornerShape(6.dp),
            colors = OutlinedTextFieldDefaults.colors(
                focusedContainerColor = FieldBackground,
                unfocusedContainerColor = FieldBackground,
                disabledContainerColor = FieldBackground,
                focusedBorderColor = LocalPefColors.current.fieldFocusedBorder,
                unfocusedBorderColor = BorderColor,
                disabledBorderColor = BorderColor,
                disabledTextColor = TextSecondary
            )
        )
    }
}