// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "DllMacro.h"
#include "utils/PluginFactory.h"
#include "viewpages/ViewStep.h"
#include <QVariantMap>
#include <QVariantList>
class QWidget; class QLabel; class QTableWidget; class QComboBox;
class QSpinBox; class QCheckBox; class QPushButton;
class PLUGINDLLEXPORT BcachefsTieringViewStep : public Calamares::ViewStep
{
    Q_OBJECT
public:
    explicit BcachefsTieringViewStep(QObject* parent = nullptr);
    ~BcachefsTieringViewStep() override;
    QString prettyName() const override;
    QString prettyStatus() const override;
    QWidget* widget() override;
    void onActivate() override;
    void onLeave() override;
    bool isNextEnabled() const override;
    bool isBackEnabled() const override { return true; }
    bool isAtBeginning() const override { return true; }
    bool isAtEnd() const override { return true; }
    Calamares::JobList jobs() const override { return {}; }
private:
    void validate();
    QVariantMap options() const;
    QWidget* m_widget = nullptr;
    QWidget* m_controls = nullptr;
    QLabel* m_info = nullptr;
    QLabel* m_error = nullptr;
    QTableWidget* m_table = nullptr;
    QComboBox* m_compression = nullptr;
    QComboBox* m_targets[4] = {};
    QSpinBox* m_data = nullptr;
    QSpinBox* m_metadata = nullptr;
    QCheckBox* m_ack = nullptr;
    QVariantList m_plan;
    QVariantList m_candidates;
    QVariantMap m_result;
    bool m_loading = false;
    bool m_active = false;
    bool m_valid = false;
};
CALAMARES_PLUGIN_FACTORY_DECLARATION(BcachefsTieringViewStepFactory)
