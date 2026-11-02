# frozen_string_literal: true

# Sample Ruby service demonstrating code smells, high complexity, and style violations
class QualityProcessor
  # Triggers Sonar ruby:S107 (too many parameters)
  def compute_metrics(param_a, param_b, param_c, param_d, param_e, param_f)
    # Triggers Sonar ruby:S1854 (Lint/UselessAssignment)
    unused_var = 123

    # Triggers Sonar ruby:S3358 (Style/NestedTernaryOperator)
    flag = if param_a
             param_b ? 1 : 2
           else
             (param_c ? 3 : 4)
           end

    # Triggers Sonar ruby:S3776 / Metrics/AbcSize & CyclomaticComplexity
    if param_a && param_b
      if param_c > 10
        param_d.times do |i|
          param_e += i if i.even?
        end
      end
    elsif param_f
      param_e = -1
    end

    # Triggers Sonar ruby:S3626 (Style/RedundantReturn)
    param_e + flag
  end

  private

  # Triggers Sonar ruby:S1144 / dead code when uncalled
  def dead_helper_method
    'never invoked'
  end
end
