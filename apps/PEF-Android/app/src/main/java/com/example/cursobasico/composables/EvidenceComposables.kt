package com.example.cursobasico.composables

import android.graphics.BitmapFactory
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.BrokenImage
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.example.cursobasico.BorderColor
import com.example.cursobasico.FieldBackground
import com.example.cursobasico.R
import com.example.cursobasico.SurfaceColor
import com.example.cursobasico.TextPrimary
import com.example.cursobasico.TextSecondary
import com.example.cursobasico.data.model.EvidenceDto
import com.example.cursobasico.ui.components.ErrorCard
import com.example.cursobasico.ui.components.InfoBanner
import com.example.cursobasico.ui.components.LoadingBox
import com.example.cursobasico.ui.components.MessageCard
import com.example.cursobasico.ui.components.StateContent
import com.example.cursobasico.ui.components.formatBytes
import com.example.cursobasico.ui.components.formatDateTime
import com.example.cursobasico.ui.components.formatTime
import com.example.cursobasico.ui.state.kind
import com.example.cursobasico.ui.viewmodel.EvidenceViewModel
import com.example.cursobasico.ui.viewmodel.EvidenceViewerState
import com.example.cursobasico.ui.viewmodel.pefViewModel
import com.example.cursobasico.views.FilterOption
import com.example.cursobasico.views.OptionFilter

@Composable
fun EvidenceCard(
    evidence: EvidenceDto,
    onClick: () -> Unit
) {
    Card(
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(10.dp),
        colors = CardDefaults.cardColors(
            containerColor = SurfaceColor
        ),
        border = BorderStroke(1.dp, BorderColor)
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp),
            verticalAlignment = Alignment.CenterVertically
        ) {
            Column(
                modifier = Modifier.weight(1f)
            ) {
                Text(
                    text = stringResource(R.string.evidence_type),
                    fontSize = 10.sp,
                    color = TextSecondary
                )
                Text(
                    text = evidence.kind.uppercase(),
                    fontSize = 10.sp,
                    color = TextPrimary
                )
                Spacer(modifier = Modifier.height(11.dp))
                Text(
                    text = formatDateTime(evidence.createdAt),
                    fontSize = 10.sp,
                    color = TextPrimary
                )
                Text(
                    text = formatBytes(evidence.sizeBytes),
                    fontSize = 10.sp,
                    color = TextPrimary
                )
            }
            Spacer(modifier = Modifier.width(9.dp))
            // Sin miniatura automática: la URL prefirmada se pide (y se audita) solo al abrir la evidencia.
            Box(
                modifier = Modifier
                    .width(105.dp)
                    .height(60.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(FieldBackground),
                contentAlignment = Alignment.Center
            ) {
                Icon(
                    imageVector = if (evidence.blocked || evidence.purged) Icons.Default.Lock else Icons.Default.Image,
                    contentDescription = null,
                    tint = TextSecondary,
                    modifier = Modifier.size(24.dp)
                )
            }
        }
    }
}

@Composable
fun EvidenceDetailField(
    label: String,
    value: String,
    modifier: Modifier = Modifier
) {
    Column(modifier = modifier) {
        Text(
            text = label,
            fontSize = 11.sp,
            color = TextSecondary
        )
        Spacer(modifier = Modifier.height(4.dp))
        Text(
            text = value,
            fontSize = 11.sp,
            fontWeight = FontWeight.Medium,
            color = TextPrimary
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EvidenceDetailSheet(
    state: EvidenceViewerState,
    onRetry: (EvidenceDto) -> Unit,
    onDismiss: () -> Unit
) {
    val evidence = when (state) {
        EvidenceViewerState.Idle -> return
        is EvidenceViewerState.Loading -> state.evidence
        is EvidenceViewerState.Image -> state.evidence
        is EvidenceViewerState.Unavailable -> state.evidence
        is EvidenceViewerState.Failed -> state.evidence
    }
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
        dragHandle = null
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .fillMaxHeight(0.85f)
                .verticalScroll(rememberScrollState())
        ) {
            // ENCABEZADO
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(54.dp)
            ) {
                Text(
                    text = stringResource(R.string.tab_evidence),
                    fontSize = 16.sp,
                    fontWeight = FontWeight.SemiBold,
                    color = TextPrimary,
                    modifier = Modifier.align(Alignment.Center)
                )
                IconButton(
                    onClick = onDismiss,
                    modifier = Modifier.align(Alignment.CenterEnd)
                ) {
                    Icon(
                        imageVector = Icons.Default.Close,
                        contentDescription = stringResource(R.string.cd_close_evidence),
                        tint = TextPrimary
                    )
                }
            }
            HorizontalDivider(
                thickness = 1.dp,
                color = BorderColor
            )
            Spacer(modifier = Modifier.height(18.dp))
            // DATOS DE LA EVIDENCIA (metadatos de EvidenceService)
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 18.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                EvidenceDetailField(
                    label = stringResource(R.string.evidence_type),
                    value = evidence.kind.uppercase(),
                    modifier = Modifier.weight(1f)
                )
                EvidenceDetailField(
                    label = stringResource(R.string.evidence_time),
                    value = formatTime(evidence.createdAt),
                    modifier = Modifier.weight(0.8f)
                )
                EvidenceDetailField(
                    label = stringResource(R.string.evidence_size),
                    value = formatBytes(evidence.sizeBytes),
                    modifier = Modifier.weight(1f)
                )
            }
            Spacer(modifier = Modifier.height(10.dp))
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 18.dp)
            ) {
                EvidenceDetailField(
                    label = stringResource(R.string.evidence_retention),
                    value = formatDateTime(evidence.retentionUntil),
                    modifier = Modifier.weight(1f)
                )
            }
            Spacer(modifier = Modifier.height(20.dp))
            // IMAGEN DE LA EVIDENCIA (URL prefirmada, solo memoria)
            Box(modifier = Modifier.padding(horizontal = 15.dp)) {
                when (state) {
                    is EvidenceViewerState.Loading -> LoadingBox()
                    is EvidenceViewerState.Image -> {
                        val bitmap = remember(state.bytes) {
                            BitmapFactory.decodeByteArray(state.bytes, 0, state.bytes.size)?.asImageBitmap()
                        }
                        if (bitmap != null) {
                            Image(
                                bitmap = bitmap,
                                contentDescription = stringResource(R.string.cd_evidence_image),
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .heightIn(max = 250.dp)
                                    .aspectRatio(bitmap.width.toFloat() / bitmap.height.coerceAtLeast(1))
                                    .clip(RoundedCornerShape(10.dp)),
                                contentScale = ContentScale.Fit
                            )
                        } else {
                            MessageCard(Icons.Default.BrokenImage, stringResource(R.string.evidence_decode_failed))
                        }
                    }
                    is EvidenceViewerState.Unavailable -> MessageCard(
                        Icons.Default.Lock,
                        stringResource(
                            if (evidence.purged) R.string.evidence_purged else R.string.evidence_blocked
                        )
                    )
                    is EvidenceViewerState.Failed -> ErrorCard(
                        kind = state.error.kind(),
                        serverMessage = state.error.serverMessage,
                        correlationId = state.error.correlationId,
                        onRetry = { onRetry(evidence) }
                    )
                    EvidenceViewerState.Idle -> Unit
                }
            }
            Spacer(modifier = Modifier.height(20.dp))
            HorizontalDivider(
                thickness = 1.dp,
                color = BorderColor
            )
            Spacer(modifier = Modifier.height(60.dp))
        }
    }
}

/** Evidencias de la sesión desde EvidenceService (GET /api/v1/sessions/{id}/evidence). */
@Composable
fun EvidenceContent(sessionId: String) {
    val viewModel = pefViewModel(key = "evidence-$sessionId") { EvidenceViewModel(sessionId, it.evidenceRepository) }
    val listState by viewModel.list.collectAsStateWithLifecycle()
    val viewer by viewModel.viewer.collectAsStateWithLifecycle()
    LaunchedEffect(sessionId) { viewModel.load() }
    var selectedKind by rememberSaveable {
        mutableStateOf<String?>(null)
    }
    Column {
        Text(
            text = stringResource(R.string.tab_evidence),
            fontSize = 12.sp,
            fontWeight = FontWeight.Bold,
            color = TextPrimary
        )
        Spacer(modifier = Modifier.height(10.dp))
        StateContent(
            state = listState,
            onRetry = viewModel::load,
            emptyText = stringResource(R.string.evidence_empty)
        ) { evidences ->
            val kinds = evidences.map { it.kind }.distinct().sorted()
            OptionFilter(
                selected = selectedKind,
                options = listOf(FilterOption(null, stringResource(R.string.filter_all_types))) +
                    kinds.map { FilterOption(it, it.uppercase()) },
                onSelected = { selectedKind = it },
                modifier = Modifier.width(130.dp)
            )
            Spacer(modifier = Modifier.height(12.dp))
            val filteredEvidences = evidences
                .filter { selectedKind == null || it.kind == selectedKind }
                .sortedByDescending { it.createdAt }
            filteredEvidences.forEach { evidence ->
                EvidenceCard(
                    evidence = evidence,
                    onClick = { viewModel.open(evidence) }
                )
                Spacer(modifier = Modifier.height(10.dp))
            }
            if (filteredEvidences.isEmpty()) {
                Text(
                    text = stringResource(R.string.evidence_empty),
                    fontSize = 11.sp,
                    color = TextSecondary,
                    modifier = Modifier.padding(12.dp)
                )
            }
            Spacer(modifier = Modifier.height(4.dp))
            InfoBanner(stringResource(R.string.evidence_inference_note))
        }
    }
    EvidenceDetailSheet(
        state = viewer,
        onRetry = viewModel::open,
        onDismiss = viewModel::close
    )
}
