// SPDX-License-Identifier: GPL-3.0-or-later
#include "BcachefsTieringViewStep.h"
#include "GlobalStorage.h"
#include "JobQueue.h"
#include <QWidget>
#include <QLabel>
#include <QVBoxLayout>
#include <QFormLayout>
#include <QTableWidget>
#include <QHeaderView>
#include <QComboBox>
#include <QSpinBox>
#include <QCheckBox>
#include <QPushButton>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QFileInfo>
#include <QScrollArea>

CALAMARES_PLUGIN_FACTORY_DEFINITION(BcachefsTieringViewStepFactory,
                                   registerPlugin<BcachefsTieringViewStep>();)
namespace {
const char* targetNames[] = {"foreground_target", "background_target", "promote_target", "metadata_target"};
}
BcachefsTieringViewStep::BcachefsTieringViewStep(QObject* parent)
    : Calamares::ViewStep(parent), m_widget(new QWidget)
{
    auto* outer = new QVBoxLayout(m_widget);
    m_info = new QLabel;
    m_info->setWordWrap(true);
    outer->addWidget(m_info);
    auto* scroll = new QScrollArea;
    scroll->setWidgetResizable(true);
    m_controls = new QWidget;
    auto* layout = new QVBoxLayout(m_controls);
    m_table = new QTableWidget(0, 4);
    m_table->setHorizontalHeaderLabels({tr("I root-grupp"), tr("Partition / disk"), tr("Montering"), tr("Diskroll")});
    m_table->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Stretch);
    m_table->setMinimumHeight(175);
    layout->addWidget(m_table);
    auto* form = new QFormLayout;
    m_compression = new QComboBox;
    m_compression->addItems({"zstd", "lz4", "none"});
    form->addRow(tr("Komprimering"), m_compression);
    m_data = new QSpinBox;
    m_data->setRange(1, 3);
    m_metadata = new QSpinBox;
    m_metadata->setRange(1, 3);
    form->addRow(tr("Datakopior"), m_data);
    form->addRow(tr("Metadatakopior"), m_metadata);
    const QString labels[] = {tr("Skriv först till"), tr("Flytta data i bakgrunden till"),
                              tr("Läscache på"), tr("Föredra metadata på")};
    for (int i = 0; i < 4; ++i) {
        m_targets[i] = new QComboBox;
        m_targets[i]->addItem(tr("Automatiskt / inget mål"), "");
        m_targets[i]->addItem("SSD", "ssd");
        m_targets[i]->addItem("HDD", "hdd");
        m_targets[i]->addItem(tr("Normal"), "normal");
        form->addRow(labels[i], m_targets[i]);
        connect(m_targets[i], &QComboBox::currentIndexChanged, this, [this] { validate(); });
    }
    layout->addLayout(form);
    auto* preset = new QPushButton(tr("Använd SSD → HDD-tiering"));
    connect(preset, &QPushButton::clicked, this, [this] {
        m_loading = true;
        for (int i = 0; i < 4; ++i) m_targets[i]->setCurrentIndex(i == 1 ? 2 : 1);
        m_loading = false;
        validate();
    });
    layout->addWidget(preset);
    auto* explanation = new QLabel(tr("Tieringmål är preferenser: bcachefs kan använda andra diskar när ett mål är fullt. "
        "En SSD i writeback-läge kan innehålla den enda aktuella datakopian. "
        "Välj två datakopior om du vill ha replikering; SSD-cache är inte en säkerhetskopia. "
        "Högst en medlem per fysisk disk stöds här."));
    explanation->setWordWrap(true);
    layout->addWidget(explanation);
    m_ack = new QCheckBox(tr("Jag förstår att valda partitioner formateras och blir ett gemensamt filsystem."));
    layout->addWidget(m_ack);
    m_error = new QLabel;
    m_error->setWordWrap(true);
    m_error->setTextFormat(Qt::PlainText);
    layout->addWidget(m_error);
    layout->addStretch();
    scroll->setWidget(m_controls);
    outer->addWidget(scroll);
    connect(m_ack, &QCheckBox::toggled, this, [this] { emit nextStatusChanged(isNextEnabled()); });
    connect(m_table, &QTableWidget::itemChanged, this, [this] { validate(); });
    connect(m_compression, &QComboBox::currentIndexChanged, this, [this] { validate(); });
    connect(m_data, &QSpinBox::valueChanged, this, [this] { validate(); });
    connect(m_metadata, &QSpinBox::valueChanged, this, [this] { validate(); });
}
BcachefsTieringViewStep::~BcachefsTieringViewStep()
{
    if (m_widget && !m_widget->parent()) delete m_widget;
}
QString BcachefsTieringViewStep::prettyName() const { return tr("Bcachefs & tiering"); }
QWidget* BcachefsTieringViewStep::widget() { return m_widget; }
bool BcachefsTieringViewStep::isNextEnabled() const { return !m_active || (m_valid && m_ack->isChecked()); }
void BcachefsTieringViewStep::onActivate()
{
    auto* gs = Calamares::JobQueue::instance()->globalStorage();
    const auto plan = gs->value("bcachefsPartitionPlan").toList();
    // Going back from Summary preserves choices, but a changed partition plan
    // always invalidates them and requires a new acknowledgement.
    if (plan == m_plan && !m_plan.isEmpty()) { validate(); return; }
    m_loading = true;
    m_plan = plan;
    m_active = false;
    m_valid = false;
    m_candidates.clear();
    m_result.clear();
    m_ack->setChecked(false);
    m_table->setRowCount(0);
    for (const auto& v : plan) {
        auto p = v.toMap();
        if (p.value("mountPoint").toString() == "/" && p.value("fs").toString() == "bcachefs") m_active = true;
        if (p.value("fs").toString() == "bcachefs" && p.value("claimed").toBool()
            && (p.value("mountPoint").toString().isEmpty() || p.value("mountPoint").toString() == "/"))
            m_candidates.append(p);
    }
    m_controls->setVisible(m_active);
    m_info->setText(m_active
        ? tr("Välj vilka bcachefs-partitioner som ska ingå i root. Extra medlemmar ska vara valda för formatering "
             "i partitioneringssteget och sakna monteringspunkt. Partitioneringsstegets formateringsval gäller även "
             "partitioner som inte ingår i gruppen. UEFI, Limine och en separat FAT32-partition på /boot krävs.")
        : tr("Ingen bcachefs-root är vald. Välj bcachefs för / i partitioneringssteget för att aktivera dessa inställningar."));
    m_table->setRowCount(m_candidates.size());
    for (int row = 0; row < m_candidates.size(); ++row) {
        const auto p = m_candidates[row].toMap();
        const bool root = p.value("mountPoint").toString() == "/";
        auto* check = new QTableWidgetItem;
        check->setFlags(root ? Qt::ItemIsEnabled : Qt::ItemIsEnabled | Qt::ItemIsUserCheckable);
        check->setCheckState(root ? Qt::Checked : Qt::Unchecked);
        m_table->setItem(row, 0, check);
        QString path = p.value("device").toString();
        if (path.isEmpty()) path = tr("Ny partition på %1, sektor %2").arg(p.value("disk").toString(), p.value("firstSector").toString());
        auto* name = new QTableWidgetItem(path);
        name->setFlags(Qt::ItemIsEnabled | Qt::ItemIsSelectable);
        m_table->setItem(row, 1, name);
        auto* mount = new QTableWidgetItem(p.value("mountPoint").toString());
        mount->setFlags(Qt::ItemIsEnabled);
        m_table->setItem(row, 2, mount);
        auto* role = new QComboBox;
        role->addItem(tr("Normal"), "normal");
        role->addItem("SSD", "ssd");
        role->addItem("HDD", "hdd");
        m_table->setCellWidget(row, 3, role);
        connect(role, &QComboBox::currentIndexChanged, this, [this] { validate(); });
    }
    for (auto* target : m_targets) target->setCurrentIndex(0);
    m_data->setValue(1);
    m_metadata->setValue(1);
    m_loading = false;
    validate();
}
QVariantMap BcachefsTieringViewStep::options() const
{
    QVariantMap result;
    QVariantList members;
    for (int row = 0; row < m_table->rowCount(); ++row) {
        if (m_table->item(row, 0)->checkState() != Qt::Checked) continue;
        auto p = m_candidates[row].toMap();
        p.insert("role", qobject_cast<QComboBox*>(m_table->cellWidget(row, 3))->currentData());
        members.append(p);
    }
    result.insert("members", members);
    result.insert("compression", m_compression->currentText());
    result.insert("data_replicas", m_data->value());
    result.insert("metadata_replicas", m_metadata->value());
    for (int i = 0; i < 4; ++i) result.insert(targetNames[i], m_targets[i]->currentData());
    return result;
}
void BcachefsTieringViewStep::validate()
{
    if (m_loading) return;
    m_valid = false;
    emit nextStatusChanged(false);
    m_result = {{"enabled", false}};
    auto* gs = Calamares::JobQueue::instance()->globalStorage();
    gs->insert("bcachefsOptions", m_result);
    if (!m_active) { emit nextStatusChanged(true); return; }
    m_ack->setChecked(false);
    QVariantMap request{{"plan", m_plan}, {"options", options()},
                        {"bootloader", gs->value("packagechooser_bootloader")},
                        {"efi", QFileInfo::exists("/sys/firmware/efi")}};
    QProcess process;
    process.start("/usr/bin/python3", {"/usr/lib/calamares/modules/bcachefsjob/core.py"});
    if (!process.waitForStarted(5000)) { m_error->setText(tr("Kunde inte starta bcachefs-valideringen.")); return; }
    process.write(QJsonDocument::fromVariant(request).toJson(QJsonDocument::Compact));
    process.closeWriteChannel();
    if (!process.waitForFinished(15000)) {
        process.kill(); process.waitForFinished();
        m_error->setText(tr("Diskkontrollen tog för lång tid."));
        emit nextStatusChanged(false); return;
    }
    const auto response = QJsonDocument::fromJson(process.readAllStandardOutput()).object().toVariantMap();
    if (!response.value("ok").toBool() || process.exitCode() != 0) {
        m_error->setText(response.value("error", tr("Diskkontrollen misslyckades.")).toString());
    } else {
        m_result = response.value("result").toMap();
        m_valid = true;
        m_error->setText(tr("Planen är giltig. Formatering sker först när du startar installationen."));
    }
    emit nextStatusChanged(isNextEnabled());
}
void BcachefsTieringViewStep::onLeave()
{
    auto result = m_result;
    result.insert("acknowledged", isNextEnabled());
    Calamares::JobQueue::instance()->globalStorage()->insert("bcachefsOptions", result);
}
QString BcachefsTieringViewStep::prettyStatus() const
{
    if (!m_active) return {};
    QStringList disks;
    for (const auto& v : m_result.value("members").toList()) {
        const auto p = v.toMap();
        disks << tr("%1, sektorer %2–%3 → %4 (FORMATERAS)")
            .arg(p.value("disk").toString(), p.value("firstSector").toString(),
                 p.value("lastSector").toString(), p.value("label").toString()).toHtmlEscaped();
    }
    QString text = disks.join("<br/>");
    text += tr("<br/>Komprimering: %1. Datakopior: %2. Metadatakopior: %3.")
        .arg(m_result.value("compression").toString()).arg(m_result.value("data_replicas").toInt())
        .arg(m_result.value("metadata_replicas").toInt());
    for (const auto* name : targetNames)
        text += QString("<br/>%1: %2").arg(name, m_result.value(name).toString().toHtmlEscaped());
    return text;
}
