package com.tradelab.android

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.viewModels
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

enum class TradeLabMode { LIVE_MARKET, OFFLINE_LAB }

class MainActivity : ComponentActivity() {
    private val vm: TradeLabViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { TradeLabApp(vm) }
    }
}

@Composable
private fun TradeLabApp(vm: TradeLabViewModel) {
    var mode by remember { mutableStateOf(TradeLabMode.LIVE_MARKET) }
    var symbol by remember { mutableStateOf("NIFTY") }
    var backendUrl by remember { mutableStateOf("") }
    val sessions by vm.sessions.collectAsState()
    val account by vm.account.collectAsState()

    MaterialTheme {
        Scaffold(topBar = {
            TopAppBar(title = { Text("TradeLab") }, actions = {
                Text(
                    if (mode == TradeLabMode.LIVE_MARKET) "LIVE / PAPER" else "OFFLINE LAB",
                    modifier = Modifier.padding(end = 16.dp)
                )
            })
        }) { pad ->
            LazyColumn(
                Modifier.fillMaxSize().padding(pad).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(14.dp)
            ) {
                item {
                    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
                        SegmentedButton(
                            selected = mode == TradeLabMode.LIVE_MARKET,
                            onClick = { mode = TradeLabMode.LIVE_MARKET },
                            shape = SegmentedButtonDefaults.itemShape(0, 2)
                        ) { Text("Live Market") }
                        SegmentedButton(
                            selected = mode == TradeLabMode.OFFLINE_LAB,
                            onClick = { mode = TradeLabMode.OFFLINE_LAB },
                            shape = SegmentedButtonDefaults.itemShape(1, 2)
                        ) { Text("Offline Lab") }
                    }
                }
                item {
                    Card {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                            Text("NIFTY + SENSEX", style = MaterialTheme.typography.titleLarge)
                            Text(
                                if (mode == TradeLabMode.LIVE_MARKET)
                                    "Angel One supplies genuine market information. Execution remains simulated."
                                else
                                    "OFFLINE — NO BROKER CONNECTION. Replay and research use the local Data Vault only."
                            )
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                FilterChip(selected = symbol == "NIFTY", onClick = { symbol = "NIFTY" }, label = { Text("NIFTY") })
                                FilterChip(selected = symbol == "SENSEX", onClick = { symbol = "SENSEX" }, label = { Text("SENSEX") })
                            }
                        }
                    }
                }
                item {
                    OutlinedTextField(
                        value = backendUrl,
                        onValueChange = { backendUrl = it },
                        label = { Text("Optional research backend URL") },
                        placeholder = { Text("http://phone-or-pc:8000") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth()
                    )
                }
                item {
                    Card {
                        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text("Paper Account", style = MaterialTheme.typography.titleLarge)
                            Text("Balance  ₹" + String.format("%,.2f", account.balance))
                            Text("Realized P&L  ₹" + String.format("%,.2f", account.realizedPnl))
                            Text("Used margin  ₹" + String.format("%,.2f", account.usedMargin))
                            Text("Initial capital  ₹" + String.format("%,.2f", account.initialBalance))
                        }
                    }
                }
                item {
                    Text("Market Data Vault", style = MaterialTheme.typography.titleLarge)
                    Text("Sessions are stored locally in Room. Live capture can populate this database; offline research must not fetch fresh broker data.")
                    Button(onClick = vm::refresh, modifier = Modifier.fillMaxWidth()) { Text("Refresh Local Sessions") }
                }
                if (sessions.isEmpty()) {
                    item { Text("No local sessions yet.") }
                } else {
                    items(sessions, key = { it.symbol + "|" + it.date }) { session ->
                        OutlinedCard(Modifier.fillMaxWidth()) {
                            Column(Modifier.padding(14.dp)) {
                                Text(session.symbol + " · " + session.date, style = MaterialTheme.typography.titleMedium)
                                Text(session.status)
                                Text("1M candles: " + session.candles + " · option snapshots: " + session.optionSnapshots)
                            }
                        }
                    }
                }
                item {
                    Text("Research pipeline", style = MaterialTheme.typography.titleLarge)
                    Text("Market → Data Vault → ONE Rule Engine → Paper Execution → Journal → Analytics → Validation")
                    Text("Historical replay/backtest uses only information available at each timestamp; missing option data remains NO_TRADE.")
                }
                item {
                    Button(
                        onClick = { mode = TradeLabMode.OFFLINE_LAB },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("Open Offline Lab") }
                }
            }
        }
    }
}
