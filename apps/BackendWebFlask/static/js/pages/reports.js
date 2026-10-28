/* Supervisor Reports charts (Highcharts), fed only with the report JSON embedded in the page
   (#report-charts, built from SessionService's report endpoints). No value is generated here.
   Colors follow the entity in a fixed order (CSS custom properties --report-series-N, light/dark);
   every chart has data labels and a table with the same numbers next to it. */
(function () {
    var source = document.getElementById("report-charts");
    if (!source || !window.Highcharts) {
        return;
    }
    var data = JSON.parse(source.textContent);
    var root = document.querySelector(".v3-page");
    var css = getComputedStyle(root);
    var page = getComputedStyle(document.documentElement);

    function token(name, fallback) {
        return (css.getPropertyValue(name) || page.getPropertyValue(name) || fallback).trim();
    }

    var series = [1, 2, 3, 4, 5, 6].map(function (n) { return token("--report-series-" + n, "#2a78d6"); });
    var text = token("--color-text-secondary", "#52514e");
    var ink = token("--color-text-primary", "#0b0b0b");
    var grid = token("--color-border", "#e5e7eb");
    var surface = token("--color-surface", "#ffffff");

    Highcharts.setOptions({
        chart: {backgroundColor: "transparent", style: {fontFamily: "inherit"}},
        title: {text: null},
        credits: {enabled: false},
        legend: {itemStyle: {color: text, fontWeight: "normal"}},
        xAxis: {labels: {style: {color: text}}, lineColor: grid, tickColor: grid},
        yAxis: {labels: {style: {color: text}}, gridLineColor: grid, title: {text: null}, allowDecimals: false, min: 0},
        tooltip: {shared: true},
        plotOptions: {
            series: {borderColor: surface, borderWidth: 2, dataLabels: {style: {color: ink, textOutline: "none", fontWeight: "normal"}}},
            column: {borderRadius: 4, dataLabels: {enabled: true}},
            bar: {borderRadius: 4, dataLabels: {enabled: true}}
        }
    });

    function chart(id, options) {
        var target = document.getElementById(id);
        if (target) {
            Highcharts.chart(target, options);
        }
    }

    // Sessions by status: one categorical slot per status (fixed order), labeled bars.
    chart("chart-status", {
        chart: {type: "bar"},
        legend: {enabled: false},
        xAxis: {categories: data.status.map(function (s) { return s.name; })},
        series: [{name: window.pefT("reportsSessions"), colorByPoint: true,
                  colors: data.status.map(function (s) { return series[s.slot - 1]; }),
                  data: data.status.map(function (s) { return s.y; })}]
    });

    // Activity by day: two series on one axis (both are session counts).
    chart("chart-activity", {
        chart: {type: "column"},
        xAxis: {categories: data.activity.categories},
        series: [
            {name: window.pefT("reportsSessions"), color: series[0], data: data.activity.sessions},
            {name: window.pefT("reportsWithDiscrepancies"), color: series[1], data: data.activity.with_discrepancies}
        ]
    });

    chart("chart-reasons", {
        chart: {type: "bar"},
        legend: {enabled: false},
        xAxis: {categories: data.reasons.categories},
        series: [{name: window.pefT("reportsDiscrepancies"), color: series[1], data: data.reasons.values}]
    });

    chart("chart-kits", {
        chart: {type: "bar"},
        legend: {enabled: false},
        xAxis: {categories: data.kits.categories},
        series: [{name: window.pefT("reportsSessions"), color: series[0], data: data.kits.values}]
    });

    chart("chart-operators", {
        chart: {type: "bar"},
        xAxis: {categories: data.operators.categories},
        series: [
            {name: window.pefT("reportsSessions"), color: series[0], data: data.operators.sessions},
            {name: window.pefT("reportsDiscrepancies"), color: series[1], data: data.operators.discrepancies}
        ]
    });
})();
