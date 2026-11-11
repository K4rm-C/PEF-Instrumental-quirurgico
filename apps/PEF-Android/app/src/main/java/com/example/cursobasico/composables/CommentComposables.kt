package com.example.cursobasico.composables

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.SpeakerNotesOff
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.cursobasico.BorderColor
import com.example.cursobasico.FieldBackground
import com.example.cursobasico.R
import com.example.cursobasico.SurfaceColor
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.TextSecondary
import com.example.cursobasico.ui.components.MessageCard
import com.example.cursobasico.ui.components.pef

/**
 * Comentarios de una sesión en revisión. ANDROID-01: el Gateway NO publica ningún endpoint de comentarios,
 * así que el diseño se conserva pero la función se muestra como no disponible (sin mensajes simulados).
 * Contrato mínimo propuesto en README.md ("Integraciones pendientes").
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CommentsSheet(
    onDismiss: () -> Unit
) {
    val sheetState = rememberModalBottomSheetState(
        skipPartiallyExpanded = true
    )
    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = SurfaceColor,
        shape = RoundedCornerShape(
            topStart = 12.dp,
            topEnd = 12.dp
        ),
        dragHandle = null,
        contentWindowInsets = {
            WindowInsets(0, 0, 0, 0)
        }
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .fillMaxHeight(0.86f)
                .imePadding()
        ) {
            // ENCABEZADO
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
            ) {
                Text(
                    text = stringResource(R.string.comments_title),
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Medium,
                    color = TextPrimary,
                    modifier = Modifier.align(Alignment.Center)
                )

                IconButton(
                    onClick = onDismiss,
                    modifier = Modifier.align(Alignment.CenterEnd)
                ) {
                    Icon(
                        imageVector = Icons.Default.Close,
                        contentDescription = stringResource(R.string.cd_close_comments),
                        tint = TextPrimary
                    )
                }
            }
            HorizontalDivider(
                thickness = 1.dp,
                color = BorderColor
            )
            // ÁREA DE COMENTARIOS
            Column(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .padding(
                        horizontal = 15.dp,
                        vertical = 42.dp
                    ),
                verticalArrangement = Arrangement.spacedBy(35.dp)
            ) {
                MessageCard(
                    icon = Icons.Default.SpeakerNotesOff,
                    title = stringResource(R.string.comments_unavailable_title),
                    detail = stringResource(R.string.comments_unavailable_detail)
                )
            }
            HorizontalDivider(
                thickness = 1.dp,
                color = BorderColor
            )
            // CAMPO INFERIOR (deshabilitado: no hay endpoint)
            CommentInputBar()
        }
    }
}

@Composable
fun CommentInputBar() {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .background(SurfaceColor)
            .padding(
                horizontal = 15.dp,
                vertical = 12.dp
            ),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        OutlinedTextField(
            value = "",
            onValueChange = {},
            enabled = false,
            modifier = Modifier
                .weight(1f)
                .heightIn(min = 48.dp),
            placeholder = {
                Text(
                    text = stringResource(R.string.comments_placeholder_unavailable),
                    fontSize = 11.sp,
                    color = TextSecondary
                )
            },
            textStyle = TextStyle(
                fontSize = 12.sp,
                color = TextPrimary
            ),
            singleLine = true,
            shape = RoundedCornerShape(6.dp),
            colors = OutlinedTextFieldDefaults.colors(
                disabledContainerColor = FieldBackground,
                disabledBorderColor = BorderColor
            ),
            keyboardOptions = KeyboardOptions(
                imeAction = ImeAction.Done
            )
        )
        Box(
            modifier = Modifier
                .size(27.dp)
                .clip(CircleShape)
                .background(pef.action.copy(alpha = 0.4f)),
            contentAlignment = Alignment.Center
        ) {
            Icon(
                imageVector = Icons.Default.Add,
                contentDescription = null,
                tint = pef.onAction,
                modifier = Modifier.size(21.dp)
            )
        }
    }
}
