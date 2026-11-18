package com.example.cursobasico.composables

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.TaskAlt
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.example.cursobasico.BorderColor
import com.example.cursobasico.FieldBackground
import com.example.cursobasico.R
import com.example.cursobasico.SurfaceColor
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.TextSecondary
import com.example.cursobasico.data.model.DiscrepancyDto
import com.example.cursobasico.ui.components.MessageCard
import com.example.cursobasico.ui.components.pef

/**
 * Formulario de resolución de discrepancia (sustituye el stub de comentarios).
 * POST /sessions/{id}/discrepancies/{id}/resolve — or_charge / SPD.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ResolveDiscrepancySheet(
    openDiscrepancies: List<DiscrepancyDto>,
    busy: Boolean,
    errorDetail: String?,
    onDismiss: () -> Unit,
    onSubmit: (discrepancyId: String, notes: String, outcome: String) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val open = openDiscrepancies
    var selectedId by rememberSaveable(open.map { it.id }) {
        mutableStateOf(open.firstOrNull()?.id.orEmpty())
    }
    val selected = open.firstOrNull { it.id == selectedId } ?: open.firstOrNull()
    val physicalShortage = selected?.reasonCode == "shortage"
    val outcomes = remember(physicalShortage) {
        if (physicalShortage) listOf("lost", "recovered") else listOf("resolved", "lost", "recovered")
    }
    var outcome by rememberSaveable(selected?.id, physicalShortage) {
        mutableStateOf(outcomes.first())
    }
    var notes by rememberSaveable(selected?.id) { mutableStateOf("") }

    ModalBottomSheet(
        onDismissRequest = onDismiss,
        sheetState = sheetState,
        containerColor = SurfaceColor,
        shape = RoundedCornerShape(topStart = 12.dp, topEnd = 12.dp),
        dragHandle = null,
        contentWindowInsets = { WindowInsets(0, 0, 0, 0) },
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .fillMaxHeight(0.9f)
                .imePadding()
        ) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
            ) {
                Text(
                    text = stringResource(R.string.resolve_sheet_title),
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Medium,
                    color = TextPrimary,
                    modifier = Modifier.align(Alignment.Center)
                )
                IconButton(onClick = onDismiss, modifier = Modifier.align(Alignment.CenterEnd)) {
                    Icon(
                        imageVector = Icons.Default.Close,
                        contentDescription = stringResource(R.string.cd_close_resolve),
                        tint = TextPrimary
                    )
                }
            }
            HorizontalDivider(thickness = 1.dp, color = BorderColor)

            Column(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 16.dp, vertical = 16.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp)
            ) {
                if (open.isEmpty()) {
                    MessageCard(
                        icon = Icons.Default.TaskAlt,
                        title = stringResource(R.string.resolve_none_title),
                        detail = stringResource(R.string.resolve_none_detail)
                    )
                } else {
                    Text(
                        text = stringResource(R.string.resolve_pick_discrepancy),
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        color = TextPrimary
                    )
                    open.forEach { disc ->
                        val label = disc.family?.name ?: disc.family?.code ?: disc.description ?: disc.id
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .selectable(
                                    selected = disc.id == selectedId,
                                    onClick = {
                                        selectedId = disc.id
                                        notes = ""
                                    },
                                    role = Role.RadioButton
                                )
                                .padding(vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            RadioButton(
                                selected = disc.id == selectedId,
                                onClick = null
                            )
                            Column(modifier = Modifier.padding(start = 8.dp)) {
                                Text(text = label, fontSize = 13.sp, color = TextPrimary)
                                disc.description?.takeIf { it.isNotBlank() }?.let {
                                    Text(text = it, fontSize = 11.sp, color = TextSecondary)
                                }
                            }
                        }
                    }

                    Text(
                        text = stringResource(R.string.resolve_outcome),
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        color = TextPrimary
                    )
                    outcomes.forEach { code ->
                        Row(
                            modifier = Modifier
                                .fillMaxWidth()
                                .selectable(
                                    selected = outcome == code,
                                    onClick = { outcome = code },
                                    role = Role.RadioButton
                                )
                                .padding(vertical = 2.dp),
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            RadioButton(selected = outcome == code, onClick = null)
                            Text(
                                text = outcomeLabel(code),
                                fontSize = 13.sp,
                                color = TextPrimary,
                                modifier = Modifier.padding(start = 8.dp)
                            )
                        }
                    }

                    Text(
                        text = stringResource(R.string.resolve_notes_label),
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        color = TextPrimary
                    )
                    OutlinedTextField(
                        value = notes,
                        onValueChange = { notes = it },
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(min = 96.dp),
                        placeholder = {
                            Text(
                                text = stringResource(R.string.resolve_notes_placeholder),
                                fontSize = 12.sp,
                                color = TextSecondary
                            )
                        },
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedContainerColor = FieldBackground,
                            unfocusedContainerColor = FieldBackground,
                            focusedBorderColor = BorderColor,
                            unfocusedBorderColor = BorderColor,
                        ),
                        shape = RoundedCornerShape(6.dp),
                    )
                    if (errorDetail != null) {
                        Text(text = errorDetail, fontSize = 12.sp, color = pef.red)
                    }
                }
            }

            HorizontalDivider(thickness = 1.dp, color = BorderColor)
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp, Alignment.End),
                verticalAlignment = Alignment.CenterVertically
            ) {
                if (busy) {
                    CircularProgressIndicator(
                        color = pef.textSecondary,
                        strokeWidth = 2.dp,
                        modifier = Modifier.size(20.dp)
                    )
                }
                Button(
                    onClick = {
                        val id = selected?.id ?: return@Button
                        if (notes.trim().isEmpty()) return@Button
                        onSubmit(id, notes.trim(), outcome)
                    },
                    enabled = !busy && selected != null && notes.trim().isNotEmpty(),
                    colors = ButtonDefaults.buttonColors(containerColor = pef.action, contentColor = pef.onAction)
                ) {
                    Text(text = stringResource(R.string.resolve_submit))
                }
            }
            Spacer(modifier = Modifier.height(8.dp))
        }
    }
}

@Composable
private fun outcomeLabel(code: String): String = when (code) {
    "resolved" -> stringResource(R.string.resolve_outcome_resolved)
    "lost" -> stringResource(R.string.resolve_outcome_lost)
    "recovered" -> stringResource(R.string.resolve_outcome_recovered)
    else -> code
}

/** Compatibilidad: el FAB antiguo abría comentarios; ahora redirige al formulario de resolución. */
@Deprecated("Use ResolveDiscrepancySheet", ReplaceWith("ResolveDiscrepancySheet(emptyList(), false, null, onDismiss) { _, _, _ -> }"))
@Composable
fun CommentsSheet(onDismiss: () -> Unit) {
    ResolveDiscrepancySheet(
        openDiscrepancies = emptyList(),
        busy = false,
        errorDetail = null,
        onDismiss = onDismiss,
        onSubmit = { _, _, _ -> },
    )
}
