package com.tradelab.android

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch

class TradeLabViewModel(app: Application) : AndroidViewModel(app) {
    private val dao = TradeLabDatabase.get(app).marketSessionDao()
    private val _sessions = MutableStateFlow<List<MarketSessionEntity>>(emptyList())
    val sessions: StateFlow<List<MarketSessionEntity>> = _sessions

    init { refresh() }

    fun refresh() {
        viewModelScope.launch { _sessions.value = dao.all() }
    }

    fun seedEmptySymbols() {
        viewModelScope.launch {
            dao.upsert(MarketSessionEntity("—", "NIFTY", "No captured session yet", 0, 0))
            dao.upsert(MarketSessionEntity("—", "SENSEX", "No captured session yet", 0, 0))
            refresh()
        }
    }
}
